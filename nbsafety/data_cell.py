# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

from .ipython_utils import cell_counter

if TYPE_CHECKING:
    from typing import Optional, Set, Tuple


class DataCell(object):
    def __init__(
            self,
            name: str,
            parents: 'Optional[Set[DataCell]]' = None,
    ):
        self.name = str(name)
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

    def update_deps(self, new_deps: 'Set[DataCell]', add=False, mark_children=True):
        if not add:
            for parent in self.parents - new_deps:
                parent.children.discard(self)
            self.parents = set()

        for new_parent in new_deps - self.parents:
            new_parent.children.add(self)
            self.parents.add(new_parent)

        self.defined_cell_num = cell_counter()
        if mark_children:
            self.update_cellnum_node_pair((cell_counter(), self))

    # TODO: don't use a tuple for this
    def update_cellnum_node_pair(self, pair: 'Tuple[int, DataCell]', seen=None):
        if seen is None:
            seen = set()
        if self in seen:
            return
        seen.add(self)
        self.required_cell_num = pair[0]
        self.fresher_ancestors.add(pair[1])
        for child in self.children:
            child.update_cellnum_node_pair(pair, seen)

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
