# -*- coding: future_annotations -*-
import ast
from collections import defaultdict
from enum import Enum
import logging
import sys
from typing import cast, TYPE_CHECKING

from nbsafety.data_model.annotation_utils import get_type_annotation, make_annotation_string
from nbsafety.data_model import sizing
from nbsafety.data_model.update_protocol import UpdateProtocol
from nbsafety.singletons import nbs, tracer

if TYPE_CHECKING:
    from nbsafety.types import SupportedIndexType
    from typing import Any, Dict, List, Optional, Set

    # avoid circular imports
    from nbsafety.data_model.scope import Scope, NamespaceScope

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


class DataSymbolType(Enum):
    DEFAULT = 'default'
    SUBSCRIPT = 'subscript'
    FUNCTION = 'function'
    CLASS = 'class'
    IMPORT = 'import'
    ANONYMOUS = 'anonymous'


class DataSymbol:

    NULL = object()

    def __init__(
        self,
        name: SupportedIndexType,
        symbol_type: DataSymbolType,
        obj: Any,
        containing_scope: Scope,
        stmt_node: Optional[ast.AST] = None,
        # TODO: also keep a reference to the target node?
        refresh_cached_obj: bool = False,
        implicit: bool = False,
    ):
        if refresh_cached_obj:
            # TODO: clean up redundancies
            assert implicit
            assert stmt_node is None
        self.name = name
        self.symbol_type = symbol_type
        self.obj = obj
        self._tombstone = False
        self._cached_out_of_sync = True
        self.cached_obj_id = None
        self.cached_obj_type = None
        if refresh_cached_obj:
            self._refresh_cached_obj()
        self.containing_scope = containing_scope
        self.stmt_node = self.update_stmt_node(stmt_node)
        self._funcall_live_symbols = None
        self.parents: Set[DataSymbol] = set()
        self.children_by_cell_position: Dict[int, Set[DataSymbol]] = defaultdict(set)

        self.call_scope: Optional[Scope] = None
        if self.is_function:
            self.call_scope = self.containing_scope.make_child_scope(self.name)

        # initialize at -1 since the corresponding piece of data could already be around,
        # and we don't want liveness checker to think this was newly created unless we
        # explicitly trace an update somewhere
        self._timestamp: int = -1
        # The version is a simple counter not associated with cells that is bumped whenever the timestamp is updated
        self._version: int = 0
        self._defined_cell_num = self._timestamp

        # The necessary last-updated timestamp / cell counter for this symbol to not be stale
        self.required_timestamp: int = self.timestamp

        # The execution counter of cell where this symbol was last used (-1 means it has net yet been used)
        self.last_used_cell_num: int = -1
        # for each usage of this dsym, the version that was used, if different from the timestamp of usage
        self.timestamp_by_used_time: Dict[int, int] = {}
        # History of definitions at time of liveness
        self.timestamp_by_liveness_time: Dict[int, int] = {}
        # All timestamps associated with this symbol
        self.updated_timestamps: Set[int] = set()

        self.fresher_ancestors: Set[DataSymbol] = set()

        # if implicitly created when tracing non-store-context ast nodes
        self._implicit = implicit

        # Will never be stale if no_warning is True
        self.disable_warnings = False
        self._temp_disable_warnings = False

        nbs().aliases[id(obj)].add(self)
        if isinstance(self.name, str) and not self.is_anonymous and not self.containing_scope.is_namespace_scope:
            ns = self.namespace
            if ns is not None and ns.scope_name == 'self':
                # hack to get a better name than `self.whatever` for fields of this object
                # not ideal because it relies on the `self` convention but is probably
                # acceptable for the use case of improving readable names
                ns.scope_name = self.name

    def __repr__(self) -> str:
        return f'<{self.readable_name}>'

    def __str__(self) -> str:
        return self.readable_name

    def __hash__(self):
        return hash(self.full_path)

    def temporary_disable_warnings(self):
        self._temp_disable_warnings = True

    @property
    def namespace_stale_symbols(self) -> Set[DataSymbol]:
        ns = self.namespace
        return set() if ns is None else ns.namespace_stale_symbols

    @property
    def timestamp_excluding_ns_descendents(self):
        return self._timestamp

    @property
    def timestamp(self) -> int:
        ts = self._timestamp
        ns = self.namespace
        return ts if ns is None else max(ts, ns.max_descendent_timestamp)

    @property
    def readable_name(self) -> str:
        return self.containing_scope.make_namespace_qualified_name(self)

    @property
    def is_subscript(self):
        return self.symbol_type == DataSymbolType.SUBSCRIPT

    @property
    def is_class(self):
        return self.symbol_type == DataSymbolType.CLASS

    @property
    def is_function(self):
        return self.symbol_type == DataSymbolType.FUNCTION

    @property
    def is_import(self):
        return self.symbol_type == DataSymbolType.IMPORT

    @property
    def imported_module(self) -> str:
        if not self.is_import:
            raise ValueError('only IMPORT symbols have `imported_module` property')
        if isinstance(self.stmt_node, ast.Import):
            for alias in self.stmt_node.names:
                name = alias.asname or alias.name
                if name == self.name:
                    return alias.name
            raise ValueError('Unable to find module for symbol %s is stmt %s' % (self, ast.dump(self.stmt_node)))
        elif isinstance(self.stmt_node, ast.ImportFrom):
            return self.stmt_node.module
        else:
            raise TypeError('Invalid stmt type for import symbol: %s' % ast.dump(self.stmt_node))

    @property
    def imported_symbol_original_name(self) -> str:
        if not self.is_import:
            raise ValueError('only IMPORT symbols have `imported_symbol_original_name` property')
        if isinstance(self.stmt_node, ast.Import):
            return self.imported_module
        elif isinstance(self.stmt_node, ast.ImportFrom):
            for alias in self.stmt_node.names:
                name = alias.asname or alias.name
                if name == self.name:
                    return alias.name
            raise ValueError('Unable to find module for symbol %s is stmt %s' % (self, ast.dump(self.stmt_node)))
        else:
            raise TypeError('Invalid stmt type for import symbol: %s' % ast.dump(self.stmt_node))

    def get_top_level(self) -> Optional[DataSymbol]:
        if not self.containing_scope.is_namespace_scope:
            return self
        else:
            containing_scope = cast('NamespaceScope', self.containing_scope)
            for alias in nbs().aliases[containing_scope.obj_id]:
                if alias.is_globally_accessible:
                    return alias.get_top_level()
            return None

    def get_import_string(self) -> str:
        if not self.is_import:
            raise ValueError('only IMPORT symbols support recreating the import string')
        module = self.imported_module
        if isinstance(self.stmt_node, ast.Import):
            if module == self.name:
                return f'import {module}'
            else:
                return f'import {module} as {self.name}'
        elif isinstance(self.stmt_node, ast.ImportFrom):
            original_symbol_name = self.imported_symbol_original_name
            if original_symbol_name == self.name:
                return f'from {module} import {original_symbol_name}'
            else:
                return f'from {module} import {original_symbol_name} as {self.name}'
        else:
            raise TypeError('Invalid stmt type for import symbol: %s' % ast.dump(self.stmt_node))

    @property
    def is_anonymous(self):
        return self.symbol_type == DataSymbolType.ANONYMOUS

    @property
    def is_implicit(self):
        return self._implicit

    def shallow_clone(self, new_obj, new_containing_scope, symbol_type):
        return self.__class__(self.name, symbol_type, new_obj, new_containing_scope)

    @property
    def obj_id(self):
        return id(self.obj)

    @property
    def obj_type(self):
        return type(self.obj)

    def get_type_annotation(self):
        return get_type_annotation(self.obj)

    def get_type_annotation_string(self) -> str:
        return make_annotation_string(self.get_type_annotation())

    @property
    def namespace(self):
        return nbs().namespaces.get(self.obj_id, None)

    @property
    def containing_namespace(self) -> Optional[NamespaceScope]:
        if self.containing_scope.is_namespace_scope:
            return cast('NamespaceScope', self.containing_scope)
        else:
            return None

    @property
    def full_path(self):
        return self.containing_scope.full_path + (self.name,)

    @property
    def full_namespace_path(self):
        return self.containing_scope.make_namespace_qualified_name(self)

    @property
    def is_garbage(self):
        return self._tombstone or self.get_ref_count() == 0

    @property
    def is_globally_accessible(self):
        return self.containing_scope.is_globally_accessible

    @property
    def is_user_accessible(self):
        return self.is_globally_accessible and not self.is_anonymous

    def collect_self_garbage(self):
        """
        Just null out the reference to obj; we need to keep the edges
        and namespace relationships around for staleness propagation.
        """
        # TODO: ideally we should figure out how to GC the symbols themselves
        #  and remove them from the symbol graph, to keep this from getting
        #  too large. One idea is that a symbol can be GC'd if its reachable
        #  descendants are all tombstone'd, and likewise a namespace can be
        #  GC'd if all of its children are GC'able as per prior criterion.
        self._tombstone = True
        ns = nbs().namespaces.get(self.obj_id, None)
        if ns is not None:
            ns._tombstone = True
            ns.obj = None
        self.obj = None

    # def update_type(self, new_type):
    #     self.symbol_type = new_type
    #     if self.is_function:
    #         self.call_scope = self.containing_scope.make_child_scope(self.name)
    #     else:
    #         self.call_scope = None

    def update_obj_ref(self, obj, refresh_cached=True):
        self._cached_out_of_sync = True
        if nbs().settings.mark_typecheck_failures_unsafe and self.cached_obj_type != type(obj):
            nbs().cell_counters_needing_typecheck |= nbs().cell_counter_by_live_symbol.get(self, set())
        self._tombstone = False
        self.obj = obj
        if self.cached_obj_id is not None and self.cached_obj_id != self.obj_id:
            if self.obj_id not in nbs().namespaces:
                # don't overwrite existing namespace for this obj
                old_ns = nbs().namespaces.get(self.cached_obj_id, None)
                if old_ns is not None:
                    _ = old_ns.fresh_copy(obj)
            self._handle_aliases()
        if refresh_cached:
            self._refresh_cached_obj()

    def get_ref_count(self):
        if self.obj is None:
            return -1
        return sys.getrefcount(self.obj) - 1 - len(nbs().aliases[self.obj_id]) - (self.obj_id in nbs().namespaces)

    def prev_obj_definitely_equal_to_current_obj(self, prev_obj: Optional[Any]) -> bool:
        if prev_obj is None:
            return False
        if not self._cached_out_of_sync or self.obj_id == self.cached_obj_id:
            return True
        if self.obj is None or prev_obj is DataSymbol.NULL:
            return self.obj is None and prev_obj is DataSymbol.NULL
        obj_type = type(self.obj)
        prev_type = type(prev_obj)
        if obj_type != prev_type:
            return False
        obj_size_ubound = sizing.sizeof(self.obj)
        if obj_size_ubound > sizing.MAX_SIZE:
            return False
        cached_obj_size_ubound = sizing.sizeof(prev_obj)
        if cached_obj_size_ubound > sizing.MAX_SIZE:
            return False
        return (obj_size_ubound == cached_obj_size_ubound) and self.obj == prev_obj

    def _handle_aliases(self):
        old_aliases = nbs().aliases.get(self.cached_obj_id, None)
        if old_aliases is not None:
            old_aliases.discard(self)
            if len(old_aliases) == 0:
                del nbs().aliases[self.cached_obj_id]
        nbs().aliases[self.obj_id].add(self)

    def update_stmt_node(self, stmt_node):
        self.stmt_node = stmt_node
        self._funcall_live_symbols = None
        if self.is_function:
            # TODO: in the case of lambdas, there will not necessarily be one
            #  symbol for a given statement. We need a more precise way to determine
            #  the symbol being called than by looking at the stmt in question.
            nbs().statement_to_func_cell[id(stmt_node)] = self
        return stmt_node

    def _refresh_cached_obj(self):
        self._cached_out_of_sync = False
        # don't keep an actual ref to avoid bumping prefcount
        self.cached_obj_id = self.obj_id
        self.cached_obj_type = self.obj_type

    def get_definition_args(self) -> List[str]:
        assert self.is_function and isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        args = []
        for arg in self.stmt_node.args.args + self.stmt_node.args.kwonlyargs:
            args.append(arg.arg)
        if self.stmt_node.args.vararg is not None:
            args.append(self.stmt_node.args.vararg.arg)
        if self.stmt_node.args.kwarg is not None:
            args.append(self.stmt_node.args.kwarg.arg)
        return args

    def _match_call_args_with_definition_args(self):
        assert self.is_function and isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        caller_node = self._get_calling_ast_node()
        if not isinstance(caller_node, ast.Call):
            return []
        def_args = self.stmt_node.args.args
        if len(self.stmt_node.args.defaults) > 0:
            def_args = def_args[:-len(self.stmt_node.args.defaults)]
        if len(def_args) > 0 and def_args[0].arg == 'self':
            # FIXME: this is bad and I should feel bad
            def_args = def_args[1:]
        for def_arg, call_arg in zip(def_args, caller_node.args):
            if isinstance(call_arg, ast.Starred):
                # give up
                # TODO: handle this case
                break
            yield def_arg.arg, tracer().resolve_loaded_symbols(call_arg)
        seen_keys = set()
        for keyword in caller_node.keywords:
            key, value = keyword.arg, keyword.value
            if value is None:
                continue
            seen_keys.add(key)
            yield key, tracer().resolve_loaded_symbols(value)
        for key, value in zip(self.stmt_node.args.args[-len(self.stmt_node.args.defaults):], self.stmt_node.args.defaults):
            if key.arg in seen_keys:
                continue
            yield key.arg, tracer().resolve_loaded_symbols(value)

    def _get_calling_ast_node(self) -> Optional[ast.AST]:
        if isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if self.name == '__getitem__':
                # TODO: handle case where we're looking for a subscript for the calling node
                return None
            for decorator in self.stmt_node.decorator_list:
                if isinstance(decorator, ast.Name) and decorator.id == 'property':
                    # TODO: handle case where we're looking for an attribute for the calling node
                    return None
        lexical_call_stack = tracer().lexical_call_stack
        if len(lexical_call_stack) == 0:
            return None
        prev_node_id_in_cur_frame_lexical = lexical_call_stack.get_field('prev_node_id_in_cur_frame_lexical')
        caller_ast_node = nbs().ast_node_by_id.get(prev_node_id_in_cur_frame_lexical, None)
        if caller_ast_node is None or not isinstance(caller_ast_node, ast.Call):
            return None
        return caller_ast_node

    def create_symbols_for_call_args(self) -> None:
        assert self.is_function
        seen_def_args = set()
        logger.info('create symbols for call to %s', self)
        for def_arg, deps in self._match_call_args_with_definition_args():
            seen_def_args.add(def_arg)
            # TODO: ideally we should try pass the actual objects to the DataSymbol ctor.
            #  Will require matching the signature with the actual call,
            #  which will be tricky I guess.
            self.call_scope.upsert_data_symbol_for_name(def_arg, None, deps, self.stmt_node, propagate=False)
            logger.info('def arg %s matched with deps %s', def_arg, deps)
        for def_arg in self.get_definition_args():
            if def_arg in seen_def_args:
                continue
            self.call_scope.upsert_data_symbol_for_name(def_arg, None, set(), self.stmt_node, propagate=False)

    @property
    def is_stale(self):
        if self.disable_warnings or self._temp_disable_warnings:
            return False
        return self.timestamp < self.required_timestamp or len(self.namespace_stale_symbols) > 0

    def should_mark_stale(self, updated_dep):
        if self.disable_warnings:
            return False
        if updated_dep is self:
            return False
        return True

    def update_deps(
        self,
        new_deps: Set[DataSymbol],
        prev_obj=None,
        overwrite=True,
        mutated=False,
        deleted=False,
        propagate_to_namespace_descendents=False,
        propagate=True,
        refresh=True,
    ):
        if self.is_import:
            # skip updates for imported symbols
            # just bump the version if it's newly created
            if self._timestamp == -1:
                self._timestamp = nbs().cell_counter()
            return
        # if we get here, no longer implicit
        self._implicit = False
        # quick last fix to avoid overwriting if we appear inside the set of deps to add (or a 1st order ancestor)
        # TODO: check higher-order ancestors too?
        overwrite = overwrite and self not in new_deps
        overwrite = overwrite and not any(self in new_dep.parents for new_dep in new_deps)
        logger.warning("symbol %s new deps %s", self, new_deps)
        new_deps.discard(self)
        if overwrite:
            for parent in self.parents - new_deps:
                for parent_children in parent.children_by_cell_position.values():
                    parent_children.discard(self)
            self.parents.clear()

        for new_parent in new_deps - self.parents:
            if new_parent is None:
                continue
            new_parent.children_by_cell_position[nbs().active_cell_position_idx].add(self)
            self.parents.add(new_parent)
        self.required_timestamp = -1
        equal_to_old = self.prev_obj_definitely_equal_to_current_obj(prev_obj)
        if mutated or isinstance(self.stmt_node, ast.AugAssign):
            self.timestamp_by_used_time[nbs().cell_counter()] = self.timestamp
        if refresh:
            self.refresh(
                bump_version=not equal_to_old,
                # rationale: if this is a mutation for which we have more precise information,
                # then we don't need to update the ns descendents as this will already have happened
                refresh_descendent_namespaces=not (mutated and not propagate_to_namespace_descendents),
                refresh_namespace_stale=not mutated,
            )
        if propagate and (mutated or deleted or not equal_to_old):
            UpdateProtocol(self)(new_deps, mutated, propagate_to_namespace_descendents)
        self._refresh_cached_obj()
        nbs().updated_symbols.add(self)

    def refresh(
        self,
        bump_version=True,
        refresh_descendent_namespaces=False,
        refresh_namespace_stale=True,
        timestamp: Optional[int] = None,
        seen: Set[DataSymbol] = None,
    ):
        self._temp_disable_warnings = False
        self.fresher_ancestors.clear()
        if bump_version:
            self._timestamp = nbs().cell_counter() if timestamp is None else timestamp
            ns = self.containing_namespace
            if ns is not None:
                ns.max_descendent_timestamp = self._timestamp
            self.updated_timestamps.add(self._timestamp)
            self._version += 1
        if refresh_descendent_namespaces:
            if seen is None:
                seen = set()
            if self in seen:
                return
            seen.add(self)
            ns = self.namespace
            if ns is not None:
                for dsym in ns.all_data_symbols_this_indentation(exclude_class=True):
                    # this is to handle cases like `x = x.mutate(42)`, where
                    # we could have changed some member of x but returned the
                    # original object -- in this case, just assume that all
                    # the stale namespace descendents are no longer stale, as
                    # this is likely the user intention. For an example, see
                    # `test_external_object_update_propagates_to_stale_namespace_symbols()`
                    # in `test_multicell_precheck.py`
                    if not dsym.is_stale or refresh_namespace_stale:
                        dsym.refresh(refresh_descendent_namespaces=True, timestamp=self._timestamp, seen=seen)
            if refresh_namespace_stale:
                self.namespace_stale_symbols.clear()
