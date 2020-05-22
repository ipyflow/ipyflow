# -*- coding: utf-8 -*-
from typing import cast, TYPE_CHECKING
import weakref

from .ipython_utils import cell_counter

if TYPE_CHECKING:
    from typing import Any, Dict, Optional, Set, Union
    from .scope import Scope, NamespaceScope


class DataCell(object):
    def __init__(
            self,
            name: 'Union[str, int]',
            obj: 'Any',
            containing_scope: 'Scope',
            parents: 'Optional[Set[DataCell]]' = None,
            is_subscript: bool = False
    ):
        self.name = name
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
        self.is_subscript = is_subscript
        self.readable_name = containing_scope.make_namespace_qualified_name(self)

        self.defined_cell_num = cell_counter()

        # The notebook cell number this is required to have to not be considered stale
        self.required_cell_num = self.defined_cell_num

        # Set of ancestors defined more recently
        self.fresher_ancestors: Set[DataCell] = set()

        #Will never be stale if no_warning is True
        self.no_warning = False

    def __repr__(self):
        return f'<{self.__class__.__name__} for variable {self.readable_name}>'

    def __str__(self):
        return self.readable_name

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

    def update_deps(
            self,
            new_deps: 'Set[DataCell]',
            add=False,
            propagate_to_children=True,
            aliases: 'Optional[Dict[int, Set[DataCell]]]' = None
    ):
        self.fresher_ancestors = set()
        self.defined_cell_num = cell_counter()
        self.required_cell_num = self.defined_cell_num
        if self.containing_scope.is_namespace_scope:
            containing_scope = cast('NamespaceScope', self.containing_scope)
            containing_scope.percolate_max_defined_timestamp(self.required_cell_num)
            containing_scope.mark_data_cell_as_not_having_stale_ancestors(self)
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

        if aliases is not None:
            assert add
            for alias in aliases[self.obj_id]:
                if alias is self:
                    continue
                alias.update_deps(new_deps, add=True, propagate_to_children=propagate_to_children)

    def mark_mutated(self, propagate_to_children=True):
        self.update_deps(set(), add=True, propagate_to_children=propagate_to_children)

    def _propagate_update(self, updated_dep: 'DataCell', seen=None):
        if seen is None:
            seen = set()
        if self in seen:
            return
        seen.add(self)
        self.required_cell_num = updated_dep.defined_cell_num
        if self.required_cell_num > self.defined_cell_num and self.containing_scope.is_namespace_scope:
            self.fresher_ancestors.add(updated_dep)
            containing_scope = cast('NamespaceScope', self.containing_scope)
            containing_scope.mark_data_cell_as_having_stale_ancestors(self)
        for child in self.children:
            child._propagate_update(updated_dep, seen=seen)

    @property
    def has_stale_ancestor(self):
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
