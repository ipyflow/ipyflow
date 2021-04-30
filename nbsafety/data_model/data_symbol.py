# -*- coding: future_annotations -*-
import ast
from collections import defaultdict
from enum import Enum
import itertools
import logging
import sys
from typing import cast, TYPE_CHECKING

from nbsafety.data_model import sizing
from nbsafety.data_model.update_protocol import UpdateProtocol
from nbsafety.singletons import nbs

if TYPE_CHECKING:
    from nbsafety.types import SupportedIndexType
    from typing import Any, Dict, Optional, Set

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
    LAMBDA = 'lambda'
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
        parents: Optional[Set[DataSymbol]] = None,
        refresh_cached_obj: bool = False,
        implicit: bool = False,
    ):
        if refresh_cached_obj:
            # TODO: clean up redundancies
            assert implicit
            assert stmt_node is None
        # print(containing_scope, name, obj, is_subscript)
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
        if parents is None:
            parents = set()
        self.parents: Set[DataSymbol] = parents
        self.children_by_cell_position: Dict[int, Set[DataSymbol]] = defaultdict(set)

        self.call_scope: Optional[Scope] = None
        if self.is_function:
            self.call_scope = self.containing_scope.make_child_scope(self.name)

        self._defined_cell_num: int = nbs().cell_counter()

        # The notebook cell number required by this symbol to not be stale
        self.required_cell_num: int = self.defined_cell_num

        # The execution counter of cell where this symbol was last used (-1 means it has net yet been used)
        self.last_used_cell_num: int = -1
        # for each usage of this dsym, the version that was used, if different from the timestamp of usage
        self.version_by_used_timestamp: Dict[int, int] = {}

        # History of definitions at time of liveness
        self.version_by_liveness_timestamp: Dict[int, int] = {}

        self.fresher_ancestors: Set[DataSymbol] = set()
        self.namespace_stale_symbols: Set[DataSymbol] = set()

        # if implicitly created when tracing non-store-context ast nodes
        self._implicit = implicit

        # Will never be stale if no_warning is True
        self.disable_warnings = False
        self._temp_disable_warnings = False

        nbs().aliases[id(obj)].add(self)

    def __repr__(self) -> str:
        return f'<{self.readable_name}>'

    def __str__(self) -> str:
        return self.readable_name

    def __hash__(self):
        return hash(self.full_path)

    def temporary_disable_warnings(self):
        self._temp_disable_warnings = True

    @property
    def defined_cell_num(self) -> int:
        # TODO: probably should be renamed
        #  (see version / timestamp below);
        #  this isn't the cell num where the symbol
        #  was defined but where it was last updated
        return self._defined_cell_num

    @defined_cell_num.setter
    def defined_cell_num(self, new_defined_cell_num: int) -> None:
        self._temp_disable_warnings = False
        self._defined_cell_num = new_defined_cell_num

    @property
    def version(self) -> int:
        return self._defined_cell_num

    @property
    def timestamp(self) -> int:
        return self._defined_cell_num

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

    @property
    def namespace(self):
        return nbs().namespaces.get(self.obj_id, None)

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
            nbs().cells_needing_typecheck |= nbs().cell_id_by_live_symbol.get(self, set())
        self._tombstone = False
        self.obj = obj
        if self.cached_obj_id is not None and self.cached_obj_id != self.obj_id:
            old_ns = nbs().namespaces.get(self.cached_obj_id, None)
            if old_ns is not None:
                _ = old_ns.fresh_copy(obj)
                old_ns._tombstone = True
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

    def get_call_args(self):
        assert self.is_function
        args = set()
        if isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # TODO: handle lambda
            for arg in self.stmt_node.args.args + self.stmt_node.args.kwonlyargs:
                args.add(arg.arg)
            if self.stmt_node.args.vararg is not None:
                args.add(self.stmt_node.args.vararg.arg)
            if self.stmt_node.args.kwarg is not None:
                args.add(self.stmt_node.args.kwarg.arg)
        return args

    def create_symbols_for_call_args(self):
        assert self.is_function
        for arg in self.get_call_args():
            # TODO: ideally we should try to pass the object here
            self.call_scope.upsert_data_symbol_for_name(arg, None, set(), self.stmt_node, False, propagate=False)

    @property
    def is_stale(self):
        if self.disable_warnings or self._temp_disable_warnings:
            return False
        return self.defined_cell_num < self.required_cell_num or len(self.namespace_stale_symbols) > 0

    def should_mark_stale(self, updated_dep):
        if self.disable_warnings:
            return False
        if updated_dep is self:
            return False
        return True

    def update_deps(
        self, new_deps: Set['DataSymbol'], prev_obj=None, overwrite=True, mutated=False, deleted=False, propagate=True
    ):
        # skip updates for imported symbols
        if self.is_import:
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
            self.parents = set()

        for new_parent in new_deps - self.parents:
            if new_parent is None:
                continue
            new_parent.children_by_cell_position[nbs().active_cell_position_idx].add(self)
            self.parents.add(new_parent)
        self.required_cell_num = -1
        UpdateProtocol(self, new_deps, prev_obj, mutated, deleted)(propagate=propagate)
        self._refresh_cached_obj()
        nbs().updated_symbols.add(self)

    def refresh(self: DataSymbol, bump_version=True):
        if bump_version:
            self.defined_cell_num = nbs().cell_counter()
        self.fresher_ancestors = set()
        self.namespace_stale_symbols = set()

    def _propagate_refresh_to_namespace_parents(self, seen: Set[DataSymbol]):
        if self in seen:
            return
        # print('refresh propagate', self)
        seen.add(self)
        for self_alias in nbs().aliases[self.obj_id]:
            containing_scope: NamespaceScope = cast('NamespaceScope', self_alias.containing_scope)
            if not containing_scope.is_namespace_scope:
                continue
            # if containing_scope.max_defined_timestamp == nbs().cell_counter():
            #     return
            containing_scope.max_defined_timestamp = nbs().cell_counter()
            containing_namespace_obj_id = containing_scope.obj_id
            # print('containing namespaces:', nbs().aliases[containing_namespace_obj_id])
            for alias in nbs().aliases[containing_namespace_obj_id]:
                alias.namespace_stale_symbols.discard(self)
                if not alias.is_stale:
                    alias.defined_cell_num = nbs().cell_counter()
                    alias.fresher_ancestors = set()
                # print('working on', alias, '; stale?', alias.is_stale, alias.namespace_stale_symbols)
                alias._propagate_refresh_to_namespace_parents(seen)
