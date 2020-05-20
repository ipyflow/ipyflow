# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING
import weakref

from .ipython_utils import cell_counter

if TYPE_CHECKING:
    from typing import Any, Optional, Set
    from .scope import Scope


class DataCell(object):
    def __init__(
            self,
            name: str,
            obj: 'Any',
            containing_scope: 'Scope',
            parents: 'Optional[Set[DataCell]]' = None,
    ):
        self.name = str(name)
        self._has_weakref = True
        try:
            self.obj_ref = weakref.ref(obj)
        except TypeError:
            self._has_weakref = False
            self.obj_ref = obj
        self.cached_obj_id = self.obj_id
        self.containing_scope = containing_scope
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

    @property
    def obj_id(self):
        if self._has_weakref:
            return id(self.obj_ref())
        else:
            return id(self.obj_ref)

    def update_obj_ref(self, obj):
        try:
            self.obj_ref = weakref.ref(obj)
            self._has_weakref = True
        except TypeError:
            self.obj_ref = obj
            self._has_weakref = False
        self.cached_obj_id = self.obj_id

    def update_deps(self, new_deps: 'Set[DataCell]', add=False, propagate_to_children=True):
        self.fresher_ancestors = set()
        self.defined_cell_num = cell_counter()
        self.required_cell_num = self.defined_cell_num
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
