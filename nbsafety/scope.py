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

from .analysis import AttrSubSymbolChain, CallPoint
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

    def get_most_specific_data_symbol_for_attrsub_chain(
            self, chain: AttrSubSymbolChain, namespaces: 'Dict[int, NamespaceScope]'
    ):
        """
        Get most specific DataSymbol for the whole chain (stops at first point it cannot find nested, e.g. a CallPoint).
        """
        cur_scope = self
        name_to_obj = get_ipython().ns_table['user_global']
        dsym = None
        obj = None
        for name in chain.symbols:
            if isinstance(name, CallPoint):
                return dsym
            dsym = cur_scope.lookup_data_symbol_by_name_this_indentation(name)
            if name_to_obj is None:
                break
            try:
                obj = Scope._get_name_to_obj_mapping(obj, dsym)[name]
            except (KeyError, IndexError, Exception):
                break
            cur_scope = namespaces.get(id(obj), None)
            if cur_scope is None:
                break
        return dsym

    def upsert_data_symbol_for_name(
            self,
            name: str,
            obj: 'Any',
            is_subscript,
            is_function_def=False,
            class_scope: 'Optional[Scope]' = None,
    ) -> 'DataSymbol':
        dc, old_dc, old_id = self._upsert_data_symbol_for_name_inner(
            name, obj, is_subscript, is_function_def=is_function_def, class_scope=class_scope
        )
        # print('upsert', name, 'with deps', deps)
        self._handle_aliases(old_id, old_dc, dc)
        return dc

    def _upsert_data_symbol_for_name_inner(
            self,
            name: str,
            obj: 'Any',
            is_subscript,
            is_function_def=False,
            class_scope: 'Optional[Scope]' = None,
    ) -> 'Tuple[DataSymbol, Optional[DataSymbol], Optional[int]]':
        assert not (class_scope is not None and is_function_def)
        symbol_type = DataSymbolType.DEFAULT
        if is_function_def:
            assert not is_subscript
            symbol_type = DataSymbolType.FUNCTION
        elif class_scope is not None:
            assert not is_subscript
            symbol_type = DataSymbolType.CLASS
        elif is_subscript:
            symbol_type = DataSymbolType.SUBSCRIPT
        old_id = None
        old_dc = self.lookup_data_symbol_by_name_this_indentation(name)
        if old_dc is not None and self.is_globally_accessible:
            old_id = old_dc.cached_obj_id
            # TODO: garbage collect old names (EDIT: does this happen automatically thanks to the handle_aliases logic?)
            if name in self.data_symbol_by_name(old_dc.is_subscript):
                old_dc.update_obj_ref(obj)
                old_dc.update_type(symbol_type)
                return old_dc, old_dc, old_id
        dc = DataSymbol(name, symbol_type, obj, self, self.safety)
        self.put(name, dc)
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
            old_alias_dcs = self.safety.aliases.get(old_id, None)
            if old_alias_dcs is not None:
                old_alias_dcs.discard(old_dc)
                if len(old_alias_dcs) == 0:
                    del self.safety.aliases[old_id]
        self.safety.aliases[dc.obj_id].add(dc)

    @property
    def is_global(self):
        return self.parent_scope is None

    @property
    def is_garbage(self):
        return False

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

    @property
    def is_garbage(self):
        return self._tombstone or self.obj_id not in self.safety.aliases or self.obj_id not in self.safety.namespaces

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

    def all_data_symbols_this_indentation(self, exclude_class=False, is_subscript=None) -> 'Iterable[DataSymbol]':
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

    def put(self, name: 'Union[str, int]', val: DataSymbol):
        if val.is_subscript:
            self._subscript_data_symbol_by_name[name] = val
        else:
            self._data_symbol_by_name[name] = val
        val.containing_scope = self

    def refresh(self):
        self.max_defined_timestamp = cell_counter()

    def get_earliest_ancestor_containing(self, obj_id: int, is_subscript: bool) -> 'Optional[NamespaceScope]':
        # TODO: test this properly
        ret = None
        if self.namespace_parent_scope is not None:
            ret = self.namespace_parent_scope.get_earliest_ancestor_containing(obj_id, is_subscript)
        if ret is not None:
            return ret
        set_to_check = map(lambda dsym: dsym.obj_id, self.all_data_symbols_this_indentation(is_subscript=is_subscript))
        if obj_id in set_to_check:
            return self
        else:
            return None

    @property
    def namespace_parent_scope(self) -> 'Optional[NamespaceScope]':
        if self.parent_scope is not None and isinstance(self.parent_scope, NamespaceScope):
            return self.parent_scope
        return None
