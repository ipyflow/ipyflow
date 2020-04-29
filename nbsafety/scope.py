# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import TYPE_CHECKING

from .data_cell import DataCell, FunctionDataCell

if TYPE_CHECKING:
    from typing import Dict, Optional, Set, Tuple


class Scope(object):
    GLOBAL_SCOPE_NAME = '<module>'

    def __init__(self, scope_name: str = GLOBAL_SCOPE_NAME, parent_scope: Optional[Scope] = None):
        self.scope_name = scope_name
        self.parent_scope = parent_scope  # None iff this is the global scope
        self.data_cell_by_name: Dict[str, DataCell] = {}

    def __hash__(self):
        return self.full_path

    def __str__(self):
        return str(self.full_path)

    def make_child_scope(self, scope_name):
        return self.__class__(scope_name, parent_scope=self)

    def lookup_data_cell_by_name(self, name):
        ret = self.data_cell_by_name.get(name, None)
        if ret is None and not self.is_global:
            ret = self.parent_scope.lookup_data_cell_by_name(name)
        return ret

    def upsert_data_cell_for_name(
            self,
            name: str,
            deps: Set[DataCell],
            add=False,
            is_function_def=False,
    ):
        if self.is_global and name in self.data_cell_by_name:
            dc = self.data_cell_by_name[name]
            dc.update_deps(deps, add=add)
            # TODO: garbage collect old names
            return dc
        if is_function_def:
            dc = FunctionDataCell(self.make_child_scope(name), name, deps)
        else:
            dc = DataCell(name, deps)
        self.data_cell_by_name[name] = dc
        for dep in deps:
            dep.children.add(dc)
        return dc

    @property
    def is_global(self):
        return self.parent_scope is None

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
