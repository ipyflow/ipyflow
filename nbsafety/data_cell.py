# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import TYPE_CHECKING

from .ipython_utils import cell_counter

if TYPE_CHECKING:
    from typing import Optional, Set, Tuple


class DataCell(object):
    def __init__(
            self,
            name: str,
            scope: str,
            parents: Optional[Set[DataCell]] = None,
    ):
        self.name = str(name)
        self.scope = scope
        if parents is None:
            parents = set()
        self.parents = parents
        self.children: Set[DataCell] = set()
        self.defined_cell_num = cell_counter()

        # The notebook cell number this is required to have to not be considered stale
        self.required_cell_num = self.defined_cell_num

        # Set of ancestors defined more recently
        self.fresher_ancestors: Set[DataCell] = set()

    def __repr__(self):
        return f'<{self.__class__.__name__} for variable {self.name}>'

    def __str__(self):
        return self.name

    def update_deps(self, new_deps: Set[DataCell], add=False):
        if not add:
            for node in self.parents - new_deps:
                node.children.remove(self)
                self.parents.remove(node)
            self.parents = set()

        for node in new_deps - self.parents:
            node.children.add(self)
            self.parents.add(node)

        self.defined_cell_num = cell_counter()
        self.update_cellnum_node_pair((cell_counter(), self))

    # TODO: don't use a tuple for this
    def update_cellnum_node_pair(self, pair: Tuple[int, DataCell], seen=None):
        if seen is None:
            seen = set()
        if self in seen:
            return
        seen.add(self)
        self.required_cell_num = pair[0]
        self.fresher_ancestors.add(pair[1])
        for n in self.children:
            n.update_cellnum_node_pair(pair, seen)


class FunctionDataCell(DataCell):
    def __init__(self, scope, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope
