# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

from .ipython_utils import cell_counter

if TYPE_CHECKING:
    from typing import Optional, Set


class DataCell(object):
    def __init__(
            self,
            name: str,
            obj_id: int,
            parents: 'Optional[Set[DataCell]]' = None,
    ):
        self.name = str(name)
        self.obj_id = obj_id
        if parents is None:
            parents = set()
        self.parents: Set[DataCell] = parents
        self.children: Set[DataCell] = set()
        self.defined_cell_num = cell_counter()

        # The notebook cell number this is required to have to not be considered stale
        self.required_cell_num = self.defined_cell_num

        # Set of ancestors defined more recently
        self.fresher_ancestors: Set[DataCell] = set()

        #Will never be stale if no_warning is True
        self.no_warning = False

    def __repr__(self):
        return f'<{self.__class__.__name__} for variable {self.name}>'

    def __str__(self):
        return self.name

    def update_deps(self, new_deps: 'Set[DataCell]', add=False, propagate_to_children=True):
        if not add:
            for parent in self.parents - new_deps:
                parent.children.discard(self)
            self.parents = set()

        for new_parent in new_deps - self.parents:
            new_parent.children.add(self)
            self.parents.add(new_parent)

        self.defined_cell_num = cell_counter()
        if propagate_to_children:
            for child in self.children:
                child._propagate_update(self)

    def mark_mutated(self, propagate_to_children=True):
        self.update_deps(set(), add=True, propagate_to_children=propagate_to_children)

    def _propagate_update(self, updated_dep: 'DataCell', seen=None):
        if seen is None:
            seen = set()
        if self in seen:
            return
        seen.add(self)
        self.required_cell_num = updated_dep.defined_cell_num
        self.fresher_ancestors.add(updated_dep)
        for child in self.children:
            child._propagate_update(updated_dep, seen=seen)

    def is_stale(self):
        if self.no_warning:
            return False
        return self.defined_cell_num < self.required_cell_num


class FunctionDataCell(DataCell):
    def __init__(self, scope, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope


class ClassDataCell(DataCell):
    def __init__(self, scope, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope
