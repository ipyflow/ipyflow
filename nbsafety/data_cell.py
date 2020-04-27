# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Optional, Set, Tuple, TYPE_CHECKING

from .ipython_utils import cell_counter

if TYPE_CHECKING:
    from .scope import Scope


class DataCell(object):
    def __init__(self, name: str, scope: Scope, defined_cell_num: Optional[int] = None):
        # The actual string name of the Node
        # Note that the DataCell should be identified by its name, thus the name should never change
        self.name = str(name)

        # The Scope class it belongs to
        self.scope = scope

        # Set of parent nodes on which this node depends
        self.parents: Set[DataCell] = set()

        # Set of children nodes that depend on this node
        self.children: Set[DataCell] = set()

        # The cell number when this node is defined
        if defined_cell_num is None:
            defined_cell_num = cell_counter()
        self.defined_cell_num = defined_cell_num

        # The cell number this node is required to have to not be considered as stale dependency
        self.required_cell_num = defined_cell_num

        # Set of ancestors defined more recently than this cell
        self.fresher_ancestors: Set[DataCell] = set()

    def __str__(self):
        return self.name

    # TODO: don't require tuple for this
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
