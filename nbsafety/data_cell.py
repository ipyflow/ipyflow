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
        self.deep_immune_children: Set[DataCell] = set()
        self.is_subscript = is_subscript
        self.readable_name = containing_scope.make_namespace_qualified_name(self)

        self.defined_cell_num = cell_counter()

        # The notebook cell number this is required to have to not be considered stale
        self.required_cell_num = self.defined_cell_num

        # Same, but for 'deep' references (i.e., if a method is called on this symbol,
        # or if this symbol is used as an argument to a function call)
        self.deep_required_cell_num = self.defined_cell_num

        self.fresher_ancestors: Set[DataCell] = set()
        self.deep_fresher_ancestors: Set[DataCell] = set()
        self.namespace_data_cells_with_stale: Set[DataCell] = set()

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
            new_deep_immune_deps: 'Set[DataCell]',
            aliases: 'Dict[int, Set[DataCell]]',
            add=False,
            propagate_to_children=True,
    ):
        self.fresher_ancestors = set()
        self.deep_fresher_ancestors = set()
        self.namespace_data_cells_with_stale = set()
        self.defined_cell_num = cell_counter()
        self.required_cell_num = self.defined_cell_num
        self.deep_required_cell_num = self.defined_cell_num
        if self.containing_scope.is_namespace_scope:
            containing_scope = cast('NamespaceScope', self.containing_scope)
            containing_scope.propagate_max_defined_timestamp(self.required_cell_num)
        if not add:
            for parent in self.parents - new_deps:
                parent.children.discard(self)
            for parent in self.parents - new_deep_immune_deps:
                parent.deep_immune_children.discard(self)
            self.parents = set()

        for new_parent in new_deps - self.parents:
            new_parent.children.add(self)
            self.parents.add(new_parent)

        for new_parent in new_deep_immune_deps - self.parents:
            new_parent.deep_immune_children.add(self)
            self.parents.add(new_parent)

        self.defined_cell_num = cell_counter()
        if propagate_to_children:
            self._propagate_update(self, aliases)

    def mark_mutated(self, aliases: 'Dict[int, Set[DataCell]]', propagate_to_children=True):
        self.update_deps(set(), set(), aliases, add=True, propagate_to_children=propagate_to_children)

    def _propagate_update(self, updated_dep: 'DataCell', aliases: 'Dict[int, Set[DataCell]]', seen=None, deep=False):
        if seen is None:
            seen = set()
        if self in seen:
            return
        seen.add(self)
        if updated_dep is not self:
            if deep:
                self.deep_required_cell_num = updated_dep.defined_cell_num
                self.deep_fresher_ancestors.add(updated_dep)
            else:
                self.required_cell_num = updated_dep.defined_cell_num
                self.fresher_ancestors.add(updated_dep)
        if self.containing_scope.is_namespace_scope:
            containing_scope = cast('NamespaceScope', self.containing_scope)
            containing_scope.max_defined_timestamp = max(
                containing_scope.max_defined_timestamp, updated_dep.defined_cell_num
            )
            namespace_obj_ref = containing_scope.namespace_obj_ref
            for alias in aliases[namespace_obj_ref]:
                alias._mark_namespace_data_cell_as_non_stale(updated_dep, aliases)
                for alias_child in alias.children_for_deep(True):
                    if alias_child.obj_id != namespace_obj_ref:
                        alias_child._propagate_update(updated_dep, aliases, seen, deep=True)
                if updated_dep is not self:
                    alias.namespace_data_cells_with_stale.add(self)

        for child in self.children_for_deep(deep):
            child._propagate_update(updated_dep, aliases, seen=seen, deep=deep)

    def _mark_namespace_data_cell_as_non_stale(self, updated_dep: 'DataCell', aliases: 'Dict[int, Set[DataCell]]'):
        if len(self.namespace_data_cells_with_stale) == 0:
            return
        self.namespace_data_cells_with_stale.discard(updated_dep)
        if len(self.namespace_data_cells_with_stale) == 0 and self.containing_scope.is_namespace_scope:
            containing_scope = cast('NamespaceScope', self.containing_scope)
            namespace_obj_ref = containing_scope.namespace_obj_ref
            for alias in aliases[namespace_obj_ref]:
                alias._mark_namespace_data_cell_as_non_stale(self, aliases)

    def children_for_deep(self, deep):
        if deep:
            return self.children
        else:
            return self.children | self.deep_immune_children

    @property
    def has_stale_ancestor(self):
        if self.no_warning:
            return False
        return self.defined_cell_num < self.required_cell_num

    @property
    def has_deep_stale_ancestor(self):
        if self.no_warning:
            return False
        return len(self.namespace_data_cells_with_stale) > 0 or self.defined_cell_num < self.deep_required_cell_num


class FunctionDataCell(DataCell):
    def __init__(self, scope, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope


class ClassDataCell(DataCell):
    def __init__(self, scope, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope
