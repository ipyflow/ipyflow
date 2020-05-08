# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

from IPython import get_ipython

from .analysis import AttributeSymbolChain, CallPoint
from .data_cell import ClassDataCell, DataCell, FunctionDataCell

if TYPE_CHECKING:
    from typing import Dict, Optional, Set, Tuple, Union


class Scope(object):
    GLOBAL_SCOPE_NAME = '<module>'

    def __init__(
            self, scope_name: str = GLOBAL_SCOPE_NAME,
            parent_scope: 'Optional[Scope]' = None,
            is_namespace_scope=False
    ):
        self.scope_name = scope_name
        self.parent_scope = parent_scope  # None iff this is the global scope
        self.is_namespace_scope = is_namespace_scope
        self.data_cell_by_name: Dict[str, DataCell] = {}

    def __hash__(self):
        return hash(self.full_path)

    def __str__(self):
        return str(self.full_path)

    def clone(self):
        cloned = Scope()
        cloned.__dict__ = dict(self.__dict__)
        # we don't want copies of the data cells but aliases instead,
        # but we still want separate dictionaries for newly created DataCells
        cloned.data_cell_by_name = dict(self.data_cell_by_name)
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

    def lookup_data_cell_by_name(self, name):
        ret = self.data_cell_by_name.get(name, None)
        if ret is None and self.non_namespace_parent_scope is not None:
            ret = self.non_namespace_parent_scope.lookup_data_cell_by_name(name)
        return ret

    def gen_data_cells_for_attr_symbol_chain(self, chain: AttributeSymbolChain, namespaces: 'Dict[int, Scope]'):
        cur_scope = self
        name_to_obj = get_ipython().ns_table['user_global']
        for name in chain.symbols:
            if isinstance(name, CallPoint):
                break
            dc = cur_scope.data_cell_by_name.get(name, None)
            if dc is not None:
                yield dc
            obj = name_to_obj.get(name, None)
            if obj is None:
                break
            cur_scope = namespaces.get(id(obj), None)
            if cur_scope is None:
                break
            name_to_obj = obj.__dict__

    def _upsert_and_mark_children_if_different_data_cell_type(
            self, dc: 'Union[ClassDataCell, FunctionDataCell]', name: str, deps: 'Set[DataCell]'
    ):
        if self.is_globally_accessible and name in self.data_cell_by_name:
            old = self.data_cell_by_name[name]
            # don't mark children as having stale dep unless old dep was of same type
            old.update_deps(set(), add=False, mark_children=isinstance(old, type(dc)))
        dc.update_deps(deps, add=False)
        self.data_cell_by_name[name] = dc
        return dc

    def _upsert_function_data_cell_for_name(self, name: str, deps: 'Set[DataCell]'):
        dc = FunctionDataCell(self.make_child_scope(name), name)
        return self._upsert_and_mark_children_if_different_data_cell_type(dc, name, deps)

    def _upsert_class_data_cell_for_name(self, name: str, deps: 'Set[DataCell]', class_scope: 'Scope'):
        dc = ClassDataCell(class_scope, name)
        return self._upsert_and_mark_children_if_different_data_cell_type(dc, name, deps)

    def upsert_data_cell_for_name(
            self,
            name: str,
            deps: 'Set[DataCell]',
            add=False,
            is_function_def=False,
            class_scope: 'Optional[Scope]' = None,
    ):
        assert not (class_scope is not None and is_function_def)
        if is_function_def:
            assert not add
            return self._upsert_function_data_cell_for_name(name, deps)
        if class_scope is not None:
            assert not add
            return self._upsert_class_data_cell_for_name(name, deps, class_scope)
        if self.is_globally_accessible and name in self.data_cell_by_name:
            # TODO: handle case where new dc is of different type
            dc = self.data_cell_by_name[name]
            dc.update_deps(deps, add=add)
            # TODO: garbage collect old names
            return dc
        dc = DataCell(name, deps)
        self.data_cell_by_name[name] = dc
        for dep in deps:
            dep.children.add(dc)
        return dc

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
