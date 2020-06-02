# -*- coding: utf-8 -*-
import inspect
import itertools
from typing import TYPE_CHECKING
import weakref

from IPython import get_ipython
try:
    import pandas
except ImportError:
    pandas = None

from .analysis import AttrSubSymbolChain, CallPoint, SymbolRef
from .data_symbol import DataSymbol, DataSymbolType
from .ipython_utils import cell_counter

if TYPE_CHECKING:
    from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union
    from .safety import DependencySafety


class Scope(object):
    GLOBAL_SCOPE_NAME = '<module>'

    def __init__(
            self,
            safety: 'DependencySafety',
            scope_name: str = GLOBAL_SCOPE_NAME,
            parent_scope: 'Optional[Scope]' = None,
    ):
        self.safety = safety
        self.scope_name = scope_name
        self.parent_scope = parent_scope  # None iff this is the global scope
        self._data_symbol_by_name: Dict[Union[str, int], DataSymbol] = {}

    def __hash__(self):
        return hash(self.full_path)

    def __str__(self):
        return str(self.full_path)

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

    def make_child_scope(self, scope_name, obj_id=None):
        if obj_id is None:
            return Scope(self.safety, scope_name, parent_scope=self)
        else:
            return NamespaceScope(obj_id, self.safety, scope_name, parent_scope=self)

    def put(self, name: 'Union[str, int]', val: DataSymbol):
        self._data_symbol_by_name[name] = val
        val.containing_scope = self

    def lookup_data_symbol_by_name_this_indentation(self, name) -> 'Optional[DataSymbol]':
        return self._data_symbol_by_name.get(name, None)

    def all_data_symbols_this_indentation(self):
        return self._data_symbol_by_name.values()

    def lookup_data_symbol_by_name(self, name):
        ret = self.lookup_data_symbol_by_name_this_indentation(name)
        if ret is None and self.non_namespace_parent_scope is not None:
            ret = self.non_namespace_parent_scope.lookup_data_symbol_by_name(name)
        return ret

    @staticmethod
    def _get_name_to_obj_mapping(obj, dc) -> 'Dict[str, Any]':
        if obj is None:
            return get_ipython().ns_table['user_global']
        elif dc is not None and dc.is_subscript:
            return obj
        else:
            try:
                name_to_obj = obj.__dict__
                if (pandas is not None) and isinstance(obj, pandas.DataFrame):
                    # FIXME: hack to get it working w/ pandas, which doesn't play nicely w/ inspect.getmembers
                    name_to_obj = dict(name_to_obj)
                    name_to_obj.update(obj.to_dict())
            except:  # noqa
                return dict(inspect.getmembers(obj))
        return name_to_obj

    def gen_data_symbols_for_attr_symbol_chain(self, chain: SymbolRef, namespaces: 'Dict[int, Scope]'):
        """
        Yield DataSymbol for the whole chain (stops at first CallPoint)
        """
        assert isinstance(chain.symbol, AttrSubSymbolChain)
        cur_scope = self
        name_to_obj = get_ipython().ns_table['user_global']
        dc = None
        obj = None
        to_yield = None
        for name in chain.symbol.symbols:
            if isinstance(name, CallPoint):
                yield dc
                # dc = cur_scope.lookup_data_symbol_by_name_this_indentation(name)
                # yield dc, False
                return
            dc = cur_scope.lookup_data_symbol_by_name_this_indentation(name)
            if dc is not None:
                # if to_yield is not None:
                #     # we only yield the last symbol in the chain as a potentially deep ref
                #     yield to_yield, False
                # save off current part of chain
                to_yield = dc
            if name_to_obj is None:
                break
            try:
                obj = Scope._get_name_to_obj_mapping(obj, dc)[name]
            except (KeyError, IndexError, Exception):
                break
            cur_scope = namespaces.get(id(obj), None)
            if cur_scope is None:
                break

        if to_yield is not None:
            yield dc

    def upsert_data_symbol_for_name(
            self,
            name: str,
            obj: 'Any',
            deps: 'Set[DataSymbol]',
            is_subscript,
            overwrite=True,
            is_function_def=False,
            class_scope: 'Optional[Scope]' = None,
    ):
        dc, old_dc, old_id = self._upsert_data_symbol_for_name_inner(
            name, obj, deps, is_subscript,
            overwrite=overwrite, is_function_def=is_function_def, class_scope=class_scope
        )
        self._handle_aliases(old_id, old_dc, dc)

    def _upsert_data_symbol_for_name_inner(
            self,
            name: str,
            obj: 'Any',
            deps: 'Set[DataSymbol]',
            is_subscript,
            overwrite=True,
            is_function_def=False,
            class_scope: 'Optional[Scope]' = None,
    ) -> 'Tuple[DataSymbol, Optional[DataSymbol], Optional[int]]':
        assert not (class_scope is not None and is_function_def)
        symbol_type = DataSymbolType.DEFAULT
        if is_function_def:
            assert overwrite
            assert not is_subscript
            symbol_type = DataSymbolType.FUNCTION
            # return self._upsert_function_data_symbol_for_name(name, obj, deps)
        elif class_scope is not None:
            assert overwrite
            assert not is_subscript
            symbol_type = DataSymbolType.CLASS
            # return self._upsert_class_data_symbol_for_name(name, obj, deps, class_scope)
        elif is_subscript:
            symbol_type = DataSymbolType.SUBSCRIPT
        old_id = None
        old_dc = self.lookup_data_symbol_by_name_this_indentation(name)
        if old_dc is not None and self.is_globally_accessible:
            old_id = old_dc.cached_obj_id
            # TODO: garbage collect old names (EDIT: does this happen automatically thanks to the handle_aliases logic?)
            # TODO: handle case where new dc is of different type
            if name in self.data_symbol_by_name(old_dc.is_subscript):
                old_dc.update_obj_ref(obj)
                old_dc.update_type(symbol_type)
                old_dc.update_deps(deps, overwrite=overwrite)
                return old_dc, old_dc, old_id
            else:
                # In this case, we are copying from a class and we need the dsym from which we are copying
                # as able to propagate to the new dsym.
                # Example:
                # class Foo:
                #     shared = 99
                # foo = Foo()
                # foo.shared = 42  # old_dc refers to Foo.shared here
                # Earlier, we were explicitly adding Foo.shared as a dependency of foo.shared as follows:
                # deps.add(old_dc)
                # But it turns out not to be necessary because foo depends on Foo, and changing Foo.shared will
                # propagate up the namespace hierarchy to Foo, which propagates to foo, which then propagates to
                # all of foo's namespace children (e.g. foo.shared).
                # This raises the question of whether we should draw the foo <-> Foo edge, since irrelevant namespace
                # children could then also be affected (e.g. some instance variable foo.x).
                # Perhaps a better strategy is to prevent propagation along this edge unless class Foo is redeclared.
                # If we do this, then we should go back to explicitly adding the dep as follows:
                # EDIT: added check to avoid propagating along class -> instance edge when class not redefined, so now
                # it is important to explicitly add this dep.
                deps.add(old_dc)
        dc = DataSymbol(name, symbol_type, obj, self, self.safety, parents=deps)
        self.put(name, dc)
        dc.update_deps(deps, overwrite=True)
        return dc, old_dc, old_id

    def _handle_aliases(
            self,
            old_id: 'Optional[int]',
            old_dc: 'Optional[DataSymbol]',
            dc: 'DataSymbol',
    ):
        if old_id == dc.obj_id and old_dc is dc:
            return
        if old_id is not None and old_dc is not None:
            old_alias_dcs = self.safety.aliases[old_id]
            old_alias_dcs.discard(old_dc)
            if len(old_alias_dcs) == 0:
                del self.safety.aliases[old_id]
        self.safety.aliases[dc.obj_id].add(dc)

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
    def full_path(self) -> 'Tuple[str, ...]':
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
            return f'{prefix}.{self.scope_name}'
        else:
            return self.scope_name

    def make_namespace_qualified_name(self, dc: 'DataSymbol'):
        return dc.name


class NamespaceScope(Scope):
    # TODO: support (multiple) inheritance by allowing
    #  NamespaceScopes from classes to clone their parent class's NamespaceScopes
    def __init__(self, obj: 'Any', *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cloned_from: Optional[NamespaceScope] = None
        self.child_clones: List[NamespaceScope] = []
        tombstone, obj_ref, obj_id = self._update_obj_ref_inner(obj)
        self._tombstone = tombstone
        self._obj_ref = obj_ref
        self.obj_id = obj_id
        self.max_defined_timestamp = 0
        self._subscript_data_symbol_by_name: Dict[Union[int, str], DataSymbol] = {}

    def update_obj_ref(self, obj):
        tombstone, obj_ref, obj_id = self._update_obj_ref_inner(obj)
        self._tombstone = tombstone
        self._obj_ref = obj_ref
        self.obj_id = obj_id

    def _update_obj_ref_inner(self, obj):
        tombstone = False
        try:
            obj_ref = weakref.ref(obj, self._obj_reference_expired_callback)
        except TypeError:
            obj_ref = None
        obj_id = id(obj)
        return tombstone, obj_ref, obj_id

    def _obj_reference_expired_callback(self, *_):
        self._tombstone = True
        self.safety.garbage_namespace_obj_ids.add(self.obj_id)

    def data_symbol_by_name(self, is_subscript=False):
        if is_subscript:
            return self._subscript_data_symbol_by_name
        else:
            return self._data_symbol_by_name

    def clone(self, obj: 'Any'):
        cloned = NamespaceScope(obj, self.safety)
        cloned.__dict__ = dict(self.__dict__)
        cloned.cloned_from = self
        cloned.update_obj_ref(obj)
        cloned._data_symbol_by_name = {}
        cloned._subscript_data_symbol_by_name = {}
        self.child_clones.append(cloned)
        return cloned

    def shallow_clone(self, obj: 'Any'):
        cloned = NamespaceScope(obj, self.safety)
        cloned.scope_name = self.scope_name
        cloned.parent_scope = self.parent_scope
        return cloned

    def make_namespace_qualified_name(self, dc: 'DataSymbol'):
        path = self.full_namespace_path
        if path:
            if dc.is_subscript:
                return f'{path}[{dc.name}]'
            else:
                return f'{path}.{dc.name}'
        else:
            return dc.name

    def lookup_data_symbol_by_name_this_indentation(self, name, is_subscript=None):
        # TODO: specify in arguments whether `name` refers to a subscript
        if is_subscript is None:
            ret = self._data_symbol_by_name.get(name, None)
            if ret is None:
                ret = self._subscript_data_symbol_by_name.get(name, None)
        elif is_subscript:
            ret = self._subscript_data_symbol_by_name.get(name, None)
        else:
            ret = self._data_symbol_by_name.get(name, None)
        if ret is None and self.cloned_from is not None:
            ret = self.cloned_from.lookup_data_symbol_by_name_this_indentation(name)
        return ret

    def all_data_symbols_this_indentation(self, exclude_class=False) -> 'Iterable[DataSymbol]':
        dsym_collections_to_chain: List[Iterable] = [
            self._data_symbol_by_name.values(), self._subscript_data_symbol_by_name.values()
        ]
        if self.cloned_from is not None and not exclude_class:
            dsym_collections_to_chain.append(self.cloned_from.all_data_symbols_this_indentation())
        return itertools.chain(*dsym_collections_to_chain)

    def put(self, name: 'Union[str, int]', val: DataSymbol):
        if val.is_subscript:
            self._subscript_data_symbol_by_name[name] = val
        else:
            self._data_symbol_by_name[name] = val
        val.containing_scope = self

    def refresh(self):
        self.max_defined_timestamp = cell_counter()

    @property
    def namespace_parent_scope(self) -> 'Optional[NamespaceScope]':
        if self.parent_scope is not None and isinstance(self.parent_scope, NamespaceScope):
            return self.parent_scope
        return None
