# -*- coding: future_annotations -*-
import ast
import inspect
import itertools
import logging
from typing import TYPE_CHECKING

from IPython import get_ipython
try:
    import pandas
except ImportError:
    pandas = None

from nbsafety.analysis.attr_symbols import AttrSubSymbolChain, CallPoint
from nbsafety.data_model.data_symbol import DataSymbol, DataSymbolType
from nbsafety.singletons import nbs

if TYPE_CHECKING:
    from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union
    from nbsafety.types import SupportedIndexType


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class Scope:
    GLOBAL_SCOPE_NAME = '<module>'

    def __init__(
        self,
        scope_name: str = GLOBAL_SCOPE_NAME,
        parent_scope: Optional[Scope] = None,
    ):
        self.scope_name = str(scope_name)
        self.parent_scope = parent_scope  # None iff this is the global scope
        self._data_symbol_by_name: Dict[SupportedIndexType, DataSymbol] = {}

    def __hash__(self):
        return hash(self.full_path)

    def __str__(self):
        return str(self.full_path)

    def __repr__(self):
        return str(self)

    def data_symbol_by_name(self, is_subscript=False):
        if is_subscript:
            raise ValueError('Only namespace scopes carry subscripts')
        return self._data_symbol_by_name

    @property
    def is_namespace_scope(self):
        return isinstance(self, NamespaceScope)

    @property
    def non_namespace_parent_scope(self):
        # a scope nested inside of a namespace scope does not have access
        # to unqualified members of the namespace scope
        if self.is_global:
            return None
        if self.parent_scope.is_namespace_scope:
            return self.parent_scope.non_namespace_parent_scope
        return self.parent_scope

    def make_child_scope(self, scope_name, obj=None) -> Scope:
        if obj is None:
            return Scope(scope_name, parent_scope=self)
        else:
            return NamespaceScope(obj, scope_name, parent_scope=self)

    def put(self, name: SupportedIndexType, val: DataSymbol):
        self._data_symbol_by_name[name] = val
        val.containing_scope = self

    def lookup_data_symbol_by_name_this_indentation(self, name, **_) -> Optional[DataSymbol]:
        return self._data_symbol_by_name.get(name, None)

    def all_data_symbols_this_indentation(self):
        return self._data_symbol_by_name.values()

    def lookup_data_symbol_by_name(self, name, **kwargs) -> Optional[DataSymbol]:
        ret = self.lookup_data_symbol_by_name_this_indentation(name, **kwargs)
        if ret is None and self.non_namespace_parent_scope is not None:
            ret = self.non_namespace_parent_scope.lookup_data_symbol_by_name(name, **kwargs)
        return ret

    @staticmethod
    def _get_name_to_obj_mapping(obj, dc) -> Dict[SupportedIndexType, Any]:
        if obj is None:
            return get_ipython().ns_table['user_global']
        elif dc is not None and dc.is_subscript:
            return obj
        else:
            try:
                if (pandas is not None) and isinstance(obj, pandas.DataFrame):
                    # FIXME: hack to get it working w/ pandas, which doesn't play nicely w/ inspect.getmembers
                    name_to_obj = {}
                    for col in obj.columns:
                        try:
                            name_to_obj[col] = getattr(obj, col)
                        except:
                            continue
                else:
                    name_to_obj = obj.__dict__
            except:  # noqa
                return dict(inspect.getmembers(obj))
        return name_to_obj

    def get_most_specific_data_symbol_for_attrsub_chain(self, chain: AttrSubSymbolChain):
        """
        Get most specific DataSymbol for the whole chain (stops at first point it cannot find nested, e.g. a CallPoint).
        """
        cur_scope = self
        dsym, next_dsym, success = None, None, False
        obj = None
        for name in chain.symbols:
            if isinstance(name, CallPoint):
                next_dsym = cur_scope.lookup_data_symbol_by_name(name.symbol)
                break
            next_dsym = cur_scope.lookup_data_symbol_by_name(name)
            if dsym is not None and next_dsym is None:
                # HUGE HACK: prevents us from checking namespace symbols unless entire namespace is stale
                # TODO: get rid of this check once namespace symbols created for dictionary literals
                if dsym.is_stale and dsym.timestamp >= dsym.required_timestamp:
                    dsym = None
                break
            dsym, next_dsym = next_dsym, None
            try:
                obj = Scope._get_name_to_obj_mapping(obj, dsym)[name]
            except (KeyError, IndexError, Exception):
                break
            cur_scope = nbs().namespaces.get(id(obj), None)
            if cur_scope is None:
                break
        else:
            success = True
        return dsym, next_dsym, success

    @staticmethod
    def _resolve_symbol_type(
        overwrite: bool = True,
        is_subscript: bool = False,
        is_function_def: bool = False,
        is_import: bool = False,
        is_anonymous: bool = False,
        class_scope: Optional[Scope] = None,
    ):
        assert not (class_scope is not None and (is_function_def or is_import))
        if is_function_def:
            assert overwrite
            assert not is_subscript
            return DataSymbolType.FUNCTION
        elif is_import:
            assert overwrite
            assert not is_subscript
            return DataSymbolType.IMPORT
        elif class_scope is not None:
            assert overwrite
            assert not is_subscript
            return DataSymbolType.CLASS
        elif is_subscript:
            return DataSymbolType.SUBSCRIPT
        elif is_anonymous:
            return DataSymbolType.ANONYMOUS
        else:
            return DataSymbolType.DEFAULT

    def upsert_data_symbol_for_name(
        self,
        name: SupportedIndexType,
        obj: Any,
        deps: Iterable[DataSymbol],
        stmt_node: ast.AST,
        overwrite: bool = True,
        is_subscript: bool = False,
        is_function_def: bool = False,
        is_import: bool = False,
        is_anonymous: bool = False,
        class_scope: Optional[Scope] = None,
        symbol_type: Optional[DataSymbolType] = None,
        propagate: bool = True,
        implicit: bool = False,
    ) -> DataSymbol:
        symbol_type = symbol_type or self._resolve_symbol_type(
            overwrite=overwrite,
            is_subscript=is_subscript,
            is_function_def=is_function_def,
            is_import=is_import,
            is_anonymous=is_anonymous,
            class_scope=class_scope
        )
        deps = set(deps)  # make a copy since we mutate it (see below fixme)
        dsym, prev_dsym, prev_obj = self._upsert_data_symbol_for_name_inner(
            name,
            obj,
            deps,  # FIXME: this updates deps, which is super super hacky
            symbol_type,
            stmt_node,
            implicit=implicit,
        )
        dsym.update_deps(
            deps, prev_obj=prev_obj, overwrite=overwrite, propagate=propagate, refresh=not implicit
        )
        return dsym

    def _upsert_data_symbol_for_name_inner(
        self,
        name: SupportedIndexType,
        obj: Any,
        deps: Set[DataSymbol],
        symbol_type: DataSymbolType,
        stmt_node: ast.AST,
        implicit: bool = False,
    ) -> Tuple[DataSymbol, Optional[DataSymbol], Optional[Any]]:
        prev_obj = None
        prev_dsym = self.lookup_data_symbol_by_name_this_indentation(
            name, is_subscript=symbol_type == DataSymbolType.SUBSCRIPT, skip_cloned_lookup=True,
        )
        if implicit and symbol_type != DataSymbolType.ANONYMOUS:
            assert prev_dsym is None, 'expected None, got %s' % prev_dsym
        if prev_dsym is not None and self.is_globally_accessible:
            prev_obj = DataSymbol.NULL if prev_dsym.obj is None else prev_dsym.obj
            # TODO: handle case where new dc is of different type
            if name in self.data_symbol_by_name(prev_dsym.is_subscript) and prev_dsym.symbol_type == symbol_type:
                prev_dsym.update_obj_ref(obj, refresh_cached=False)
                # old_dsym.update_type(symbol_type)
                # if we're updating a pre-existing one, it should not be an implicit upsert
                assert stmt_node is not None
                prev_dsym.update_stmt_node(stmt_node)
                return prev_dsym, prev_dsym, prev_obj
            else:
                # In this case, we are copying from a class and we need the dsym from which we are copying
                # as able to propagate to the new dsym.
                # Example:
                # class Foo:
                #     shared = 99
                # foo = Foo()
                # foo.shared = 42  # old_dsym refers to Foo.shared here
                # Earlier, we were explicitly adding Foo.shared as a dependency of foo.shared as follows:
                # deps.add(old_dsym)
                # But it turns out not to be necessary because foo depends on Foo, and changing Foo.shared will
                # propagate up the namespace hierarchy to Foo, which propagates to foo, which then propagates to
                # all of foo's namespace children (e.g. foo.shared).
                # This raises the question of whether we should draw the foo <-> Foo edge, since irrelevant namespace
                # children could then also be affected (e.g. some instance variable foo.x).
                # Perhaps a better strategy is to prevent propagation along this edge unless class Foo is redeclared.
                # If we do this, then we should go back to explicitly adding the dep as follows:
                # EDIT: added check to avoid propagating along class -> instance edge when class not redefined, so now
                # it is important to explicitly add this dep.
                deps.add(prev_dsym)
        if isinstance(self, NamespaceScope) and symbol_type == DataSymbolType.DEFAULT and self.cloned_from is not None:
            # add the cloned symbol as a dependency of the symbol about to b ecreated
            new_dep = self.cloned_from.lookup_data_symbol_by_name_this_indentation(name, is_subscript=False)
            if new_dep is not None:
                deps.add(new_dep)
        dsym = DataSymbol(
            name, symbol_type, obj, self, stmt_node=stmt_node, refresh_cached_obj=False, implicit=implicit
        )
        self.put(name, dsym)
        return dsym, prev_dsym, prev_obj

    def delete_data_symbol_for_name(self, name: SupportedIndexType, is_subscript: bool = False):
        dsym = self._data_symbol_by_name.pop(name, None)
        if dsym is not None:
            dsym.update_deps(set())

    @property
    def is_global(self):
        return self.parent_scope is None

    @property
    def is_globally_accessible(self):
        return self.is_global or (self.is_namespace_scope and self.parent_scope.is_globally_accessible)

    @property
    def global_scope(self):
        if self.is_global:
            return self
        return self.parent_scope.global_scope

    @property
    def full_path(self) -> Tuple[str, ...]:
        path = (self.scope_name,)
        if self.is_global:
            return path
        else:
            return self.parent_scope.full_path + path

    @property
    def full_namespace_path(self) -> str:
        if not self.is_namespace_scope:
            return ''
        if self.parent_scope is not None:
            prefix = self.parent_scope.full_namespace_path
        else:
            prefix = ''
        if prefix:
            if getattr(self, 'is_subscript', False):
                return f'{prefix}[{self.scope_name}]'
            else:
                return f'{prefix}.{self.scope_name}'
        else:
            return self.scope_name

    def make_namespace_qualified_name(self, dsym: DataSymbol) -> str:
        return str(dsym.name)


class NamespaceScope(Scope):
    ANONYMOUS = '<anonymous_namespace>'

    # TODO: support (multiple) inheritance by allowing
    #  NamespaceScopes from classes to clone their parent class's NamespaceScopes
    def __init__(self, obj: Any, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cloned_from: Optional[NamespaceScope] = None
        self.child_clones: List[NamespaceScope] = []
        self.obj = obj
        self.cached_obj_id = id(obj)
        if obj is not None and not isinstance(obj, int) and id(obj) in nbs().namespaces:  # pragma: no cover
            msg = 'namespace already registered for %s' % obj
            if nbs().is_develop:
                raise ValueError(msg)
            else:
                logger.warning(msg)
        nbs().namespaces[id(obj)] = self
        self._tombstone = False
        # this timestamp needs to be bumped in DataSymbol refresh()
        self.max_descendent_timestamp = -1
        self._subscript_data_symbol_by_name: Dict[SupportedIndexType, DataSymbol] = {}
        self.namespace_stale_symbols: Set[DataSymbol] = set()

    def __bool__(self):
        # in order to override if __len__ returns 0
        return True

    def __len__(self):
        if not isinstance(self.obj, (dict, list, tuple)):  # pragma: no cover
            raise TypeError("tried to get length of non-container namespace %s: %s", self, self.obj)
        return len(self.obj)

    def _iter_inner(self):
        for i in range(len(self.obj)):
            yield self.lookup_data_symbol_by_name_this_indentation(i, is_subscript=True)

    def __iter__(self):
        if not isinstance(self.obj, (list, tuple)):  # pragma: no cover
            raise TypeError("tried to iterate through non-sequence namespace %s: %s", self, self.obj)
        # do the validation before starting the generator part so that we raise immediately
        return self._iter_inner()

    def _items_inner(self):
        for key in self.obj.keys():
            yield key, self.lookup_data_symbol_by_name_this_indentation(key, is_subscript=True)

    def items(self):
        if not isinstance(self.obj, dict):  # pragma: no cover
            raise TypeError("tried to get iterate through items of non-dict namespace: %s", self.obj)
        # do the validation before starting the generator part so that we raise immediately
        return self._items_inner()

    @property
    def obj_id(self):
        return self.cached_obj_id

    @property
    def is_anonymous(self):
        return self.scope_name == NamespaceScope.ANONYMOUS

    @property
    def is_garbage(self):
        return self._tombstone or self.obj_id not in nbs().aliases or self.obj_id not in nbs().namespaces

    @property
    def is_subscript(self):
        dsym = nbs().get_first_full_symbol(self.obj_id)
        if dsym is None:
            return False
        else:
            return dsym.is_subscript

    def update_obj_ref(self, obj):
        self._tombstone = False
        nbs().namespaces.pop(self.cached_obj_id, None)
        self.obj = obj
        self.cached_obj_id = id(obj)
        nbs().namespaces[self.cached_obj_id] = self

    def data_symbol_by_name(self, is_subscript=False):
        if is_subscript:
            return self._subscript_data_symbol_by_name
        else:
            return self._data_symbol_by_name

    def clone(self, obj: Any):
        cloned = NamespaceScope(obj, self.scope_name, self.parent_scope)
        cloned.cloned_from = self
        self.child_clones.append(cloned)
        return cloned

    def fresh_copy(self, obj: Any):
        return NamespaceScope(obj, self.scope_name, self.parent_scope)

    def make_namespace_qualified_name(self, dsym: DataSymbol):
        path = self.full_namespace_path
        name = str(dsym.name)
        if path:
            if dsym.is_subscript:
                return f'{path}[{name}]'
            else:
                return f'{path}.{name}'
        else:
            return name

    def lookup_data_symbol_by_name_this_indentation(self, name, is_subscript=None, skip_cloned_lookup=False):
        # TODO: specify in arguments whether `name` refers to a subscript
        if is_subscript is None:
            ret = self._data_symbol_by_name.get(name, None)
            if ret is None:
                ret = self._subscript_data_symbol_by_name.get(name, None)
        elif is_subscript:
            ret = self._subscript_data_symbol_by_name.get(name, None)
        else:
            ret = self._data_symbol_by_name.get(name, None)
        if not skip_cloned_lookup and ret is None and self.cloned_from is not None and not is_subscript and isinstance(name, str):
            if name not in getattr(self.obj, '__dict__', {}):
                # only fall back to the class sym if it's not present in the corresponding obj for this scope
                ret = self.cloned_from.lookup_data_symbol_by_name_this_indentation(name, is_subscript=is_subscript)
        return ret

    def delete_data_symbol_for_name(self, name: SupportedIndexType, is_subscript: bool = False):
        if is_subscript:
            dsym = self._subscript_data_symbol_by_name.pop(name, None)
            if dsym is None and name == -1 and isinstance(self.obj, list):
                name = len(self.obj)  # it will have already been deleted, so don't subtract 1
                dsym = self._subscript_data_symbol_by_name.pop(name, None)
            if dsym is not None:
                dsym.update_deps(set(), deleted=True)
        else:
            super().delete_data_symbol_for_name(name)

    def all_data_symbols_this_indentation(self, exclude_class=False, is_subscript=None) -> Iterable[DataSymbol]:
        if is_subscript is None:
            dsym_collections_to_chain: List[Iterable] = [
                self._data_symbol_by_name.values(), self._subscript_data_symbol_by_name.values()
            ]
        elif is_subscript:
            dsym_collections_to_chain = [self._subscript_data_symbol_by_name.values()]
        else:
            dsym_collections_to_chain = [self._data_symbol_by_name.values()]
        if self.cloned_from is not None and not exclude_class:
            dsym_collections_to_chain.append(self.cloned_from.all_data_symbols_this_indentation())
        return itertools.chain(*dsym_collections_to_chain)

    def put(self, name: SupportedIndexType, val: DataSymbol):
        if val.is_subscript:
            self._subscript_data_symbol_by_name[name] = val
        elif not isinstance(name, str):  # pragma: no cover
            raise TypeError('%s should be a string' % name)
        else:
            self._data_symbol_by_name[name] = val
        val.containing_scope = self

    def refresh(self):
        self.max_descendent_timestamp = nbs().cell_counter()

    def get_earliest_ancestor_containing(self, obj_id: int, is_subscript: bool) -> Optional[NamespaceScope]:
        # TODO: test this properly
        ret = None
        if self.namespace_parent_scope is not None:
            ret = self.namespace_parent_scope.get_earliest_ancestor_containing(obj_id, is_subscript)
        if ret is not None:
            return ret
        if obj_id in (dsym.obj_id for dsym in self.all_data_symbols_this_indentation(is_subscript=is_subscript)):
            return self
        else:
            return None

    @property
    def namespace_parent_scope(self) -> Optional[NamespaceScope]:
        if self.parent_scope is not None and isinstance(self.parent_scope, NamespaceScope):
            return self.parent_scope
        return None
