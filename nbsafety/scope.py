# -*- coding: utf-8 -*-
import inspect
import itertools
from typing import TYPE_CHECKING

from IPython import get_ipython
try:
    import pandas
except ImportError:
    pandas = None

from .analysis import AttrSubSymbolChain, CallPoint, SymbolRef
from .data_symbol import ClassDataSymbol, DataSymbol, FunctionDataSymbol

if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional, Set, Tuple, Union
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

    def make_child_scope(self, scope_name, namespace_obj_ref=None):
        if namespace_obj_ref is None:
            return Scope(self.safety, scope_name, parent_scope=self)
        else:
            return NamespaceScope(namespace_obj_ref, self.safety, scope_name, parent_scope=self)

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

    def gen_data_symbols_for_attr_symbol_chain(self, chain: SymbolRef, namespaces: 'Dict[int, Scope]'):
        """
        Yield DataSymbols in the chain as well as whether they are deep references
        """
        assert isinstance(chain.symbol, AttrSubSymbolChain)
        cur_scope = self
        name_to_obj = get_ipython().ns_table['user_global']
        dc = None
        to_yield = None
        for name in chain.symbol.symbols:
            if isinstance(name, CallPoint):
                yield dc, True
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
                obj = name_to_obj[name]
            except (KeyError, IndexError, Exception):
                break
            cur_scope = namespaces.get(id(obj), None)
            if cur_scope is None:
                break

            try:
                name_to_obj = obj.__dict__
                if (pandas is not None) and isinstance(obj, pandas.DataFrame):
                    # FIXME: hack to get it working w/ pandas, which doesn't play nicely w/ inspect.getmembers
                    name_to_obj = dict(name_to_obj)
                    name_to_obj.update(obj.to_dict())
            except:  # noqa
                try:
                    name_to_obj = inspect.getmembers(obj)
                except:  # noqa
                    name_to_obj = None
        if to_yield is not None:
            yield dc, chain.deep

    def _upsert_and_mark_children_if_same_data_symbol_type(
            self, dc: 'Union[ClassDataSymbol, FunctionDataSymbol]', name: str, deps: 'Set[DataSymbol]',
    ) -> 'Tuple[DataSymbol, DataSymbol, Optional[int]]':
        old_id = None
        old_dc = None
        should_propagate = False
        if self.is_globally_accessible:
            old_dc = self.lookup_data_symbol_by_name_this_indentation(name)
            if old_dc is not None:
                for child in old_dc.children:
                    child.parents.discard(old_dc)
                    child.fresher_ancestors.discard(old_dc)
                old_id = old_dc.cached_obj_id
                # don't mark children as having stale dep unless old dep was of same type
                should_propagate = isinstance(old_dc, type(dc))
        if should_propagate and old_dc is not None:
            dc.children = old_dc.children
            for child in dc.children:
                child.parents.add(dc)
        dc.update_deps(deps, overwrite=True)
        self.put(name, dc)
        return dc, old_dc, old_id

    def _upsert_function_data_symbol_for_name(
            self, name: str, obj: 'Any', deps: 'Set[DataSymbol]',
    ):
        dc = FunctionDataSymbol(name, obj, self, self.safety)
        return self._upsert_and_mark_children_if_same_data_symbol_type(dc, name, deps)

    def _upsert_class_data_symbol_for_name(
            self, name: str, obj: 'Any', deps: 'Set[DataSymbol]', class_scope: 'Scope'
    ):
        dc = ClassDataSymbol(name, obj, self, self.safety, class_scope=class_scope)
        return self._upsert_and_mark_children_if_same_data_symbol_type(dc, name, deps)

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
    ) -> 'Tuple[DataSymbol, DataSymbol, Optional[int]]':
        assert not (class_scope is not None and is_function_def)
        if is_function_def:
            assert overwrite
            assert not is_subscript
            return self._upsert_function_data_symbol_for_name(name, obj, deps)
        if class_scope is not None:
            assert overwrite
            assert not is_subscript
            return self._upsert_class_data_symbol_for_name(name, obj, deps, class_scope)
        old_id = None
        old_dc = None
        if self.is_globally_accessible:
            old_dc = self.lookup_data_symbol_by_name_this_indentation(name)
            if old_dc is not None:
                old_id = old_dc.cached_obj_id
                # TODO: garbage collect old names
                # TODO: handle case where new dc is of different type
                if name in self.data_symbol_by_name(old_dc.is_subscript):
                    old_dc.update_obj_ref(obj)
                    old_dc.update_deps(deps, overwrite=overwrite)
                    return old_dc, old_dc, old_id
                else:
                    # in this case, we are copying from a class and should add the dc from which we are copying
                    # as an additional dependency
                    deps.add(old_dc)
        dc = DataSymbol(name, obj, self, self.safety, parents=deps, is_subscript=is_subscript)
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
    def __init__(self, namespace_obj_ref: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cloned_from: Optional[NamespaceScope] = None
        self.child_clones: List[NamespaceScope] = []
        self.namespace_obj_ref = namespace_obj_ref
        self.max_defined_timestamp = 0
        self._subscript_data_symbol_by_name: Dict[Union[int, str], DataSymbol] = {}

    def data_symbol_by_name(self, is_subscript=False):
        if is_subscript:
            return self._subscript_data_symbol_by_name
        else:
            return self._data_symbol_by_name

    def clone(self, namespace_obj_ref: int):
        cloned = NamespaceScope(namespace_obj_ref, self.safety)
        cloned.__dict__ = dict(self.__dict__)
        cloned.cloned_from = self
        cloned.namespace_obj_ref = namespace_obj_ref
        cloned._data_symbol_by_name = {}
        cloned._subscript_data_symbol_by_name = {}
        self.child_clones.append(cloned)
        return cloned

    def shallow_clone(self, namespace_obj_ref: int):
        cloned = NamespaceScope(namespace_obj_ref, self.safety)
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

    def lookup_data_symbol_by_name_this_indentation(self, name):
        # TODO: specify in arguments whether `name` refers to a subscript
        ret = self._data_symbol_by_name.get(name, None)
        if ret is None:
            ret = self._subscript_data_symbol_by_name.get(name, None)
        if ret is None and self.cloned_from is not None:
            ret = self.cloned_from.lookup_data_symbol_by_name_this_indentation(name)
        return ret

    def all_data_symbols_this_indentation(self):
        dsym_collections_to_chain = [self._data_symbol_by_name.values(), self._subscript_data_symbol_by_name.values()]
        if self.cloned_from is not None:
            dsym_collections_to_chain.append(self.cloned_from.all_data_symbols_this_indentation())
        return itertools.chain(*dsym_collections_to_chain)

    def put(self, name: 'Union[str, int]', val: DataSymbol):
        if val.is_subscript:
            self._subscript_data_symbol_by_name[name] = val
        else:
            self._data_symbol_by_name[name] = val
        val.containing_scope = self

    @property
    def namespace_parent_scope(self) -> 'Optional[NamespaceScope]':
        if self.parent_scope is not None and isinstance(self.parent_scope, NamespaceScope):
            return self.parent_scope
        return None
