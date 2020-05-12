# -*- coding: utf-8 -*-
import inspect
from typing import TYPE_CHECKING

from IPython import get_ipython
try:
    import pandas
except ImportError:
    pandas = None

from .analysis import AttributeSymbolChain, CallPoint
from .data_cell import ClassDataCell, DataCell, FunctionDataCell

if TYPE_CHECKING:
    from typing import Dict, Optional, Set, Tuple, Union


class Scope(object):
    GLOBAL_SCOPE_NAME = '<module>'

    def __init__(
            self, scope_name: str = GLOBAL_SCOPE_NAME,
            parent_scope: 'Optional[Scope]' = None,
            is_namespace_scope=False,
    ):
        self.scope_name = scope_name
        self.parent_scope = parent_scope  # None iff this is the global scope
        self.cloned_from = None
        self.is_namespace_scope = is_namespace_scope
        self._data_cell_by_name: Dict[str, DataCell] = {}

    def __hash__(self):
        return hash(self.full_path)

    def __str__(self):
        return str(self.full_path)

    def clone(self):
        cloned = Scope()
        cloned.__dict__ = dict(self.__dict__)
        cloned.cloned_from = self
        cloned._data_cell_by_name = {}
        return cloned

    @property
    def non_namespace_parent_scope(self):
        # a scope nested inside of a namespace scope does not have access
        # to unqualified members of the namespace scope
        if self.is_global:
            return None
        if self.parent_scope.is_namespace_scope:
            return self.parent_scope.non_namespace_parent_scope
        return self.parent_scope

    def make_child_scope(self, scope_name, is_namespace_scope=False):
        return self.__class__(scope_name, parent_scope=self, is_namespace_scope=is_namespace_scope)

    def put(self, name: str, val: DataCell):
        self._data_cell_by_name[name] = val

    def lookup_data_cell_by_name_this_indentation(self, name):
        ret = self._data_cell_by_name.get(name, None)
        if ret is None and self.cloned_from is not None:
            ret = self.cloned_from.lookup_data_cell_by_name_this_indentation(name)
        return ret

    def all_data_cells_this_indentation(self):
        if self.cloned_from is None:
            ret = {}
        else:
            ret = self.cloned_from.all_data_cells_this_indentation()
        ret.update(self._data_cell_by_name)
        return ret

    def lookup_data_cell_by_name(self, name):
        ret = self.lookup_data_cell_by_name_this_indentation(name)
        if ret is None and self.non_namespace_parent_scope is not None:
            ret = self.non_namespace_parent_scope.lookup_data_cell_by_name(name)
        return ret

    def gen_data_cells_for_attr_symbol_chain(self, chain: AttributeSymbolChain, namespaces: 'Dict[int, Scope]'):
        cur_scope = self
        name_to_obj = get_ipython().ns_table['user_global']
        for name in chain.symbols:
            if isinstance(name, CallPoint):
                break
            dc = cur_scope.lookup_data_cell_by_name_this_indentation(name)
            if dc is not None:
                yield dc
            if name_to_obj is None:
                break
            obj = name_to_obj.get(name, None)
            if obj is None:
                break
            cur_scope = namespaces.get(id(obj), None)
            if cur_scope is None:
                break
            
            if (pandas is not None) and isinstance(obj, pandas.DataFrame):
                # FIXME: hack to get it working w/ pandas, which doesn't play nicely w/ inspect.getmembers
                name_to_obj = obj.__dict__
                name_to_obj.update(obj.to_dict())
            else:
                name_to_obj = dict(inspect.getmembers(obj))

    def _upsert_and_mark_children_if_different_data_cell_type(
            self, dc: 'Union[ClassDataCell, FunctionDataCell]', name: str, deps: 'Set[DataCell]'
    ) -> 'Tuple[DataCell, DataCell, Optional[int]]':
        old_id = None
        old_dc = None
        if self.is_globally_accessible:
            old_dc = self.lookup_data_cell_by_name_this_indentation(name)
            if old_dc is not None:
                old_id = old_dc.obj_id
                # don't mark children as having stale dep unless old dep was of same type
                old_dc.update_deps(set(), add=False, propagate_to_children=isinstance(old_dc, type(dc)))
        dc.update_deps(deps, add=False)
        self.put(name, dc)
        return dc, old_dc, old_id

    def _upsert_function_data_cell_for_name(self, name: str, obj_id: int, deps: 'Set[DataCell]'):
        dc = FunctionDataCell(self.make_child_scope(name), name, obj_id)
        return self._upsert_and_mark_children_if_different_data_cell_type(dc, name, deps)

    def _upsert_class_data_cell_for_name(self, name: str, obj_id: int, deps: 'Set[DataCell]', class_scope: 'Scope'):
        dc = ClassDataCell(class_scope, name, obj_id)
        return self._upsert_and_mark_children_if_different_data_cell_type(dc, name, deps)

    def upsert_data_cell_for_name(
            self,
            name: str,
            obj_id: int,
            deps: 'Set[DataCell]',
            add=False,
            is_function_def=False,
            class_scope: 'Optional[Scope]' = None,
    ) -> 'Tuple[DataCell, DataCell, Optional[int]]':
        assert not (class_scope is not None and is_function_def)
        if is_function_def:
            assert not add
            return self._upsert_function_data_cell_for_name(name, obj_id, deps)
        if class_scope is not None:
            assert not add
            return self._upsert_class_data_cell_for_name(name, obj_id, deps, class_scope)
        old_id = None
        old_dc = None
        if self.is_globally_accessible:
            old_dc = self.lookup_data_cell_by_name_this_indentation(name)
            if old_dc is not None:
                old_id = old_dc.obj_id
                # TODO: garbage collect old names
                # TODO: handle case where new dc is of different type
                if name in self._data_cell_by_name:
                    old_dc.update_deps(deps, add=add)
                    old_dc.obj_id = obj_id
                    return old_dc, old_dc, old_id
                else:
                    # in this case, we are copying from a class and should add the dc from which we are copying
                    # as an additional dependency
                    deps.add(old_dc)
        dc = DataCell(name, obj_id, deps)
        self.put(name, dc)
        for dep in deps:
            dep.children.add(dc)
        return dc, old_dc, old_id

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
