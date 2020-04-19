# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scope import Scope


class VariableNode(object):
    def __init__(self, name: str, defined_CN: int, scope: Scope, uid: int, aliasable: bool):
        # The actual string name of the Node
        # Note that the VariableNode should be identified by its name, thus the name should never change
        self.name = str(name)

        # The Scope class it belongs to
        self.scope = scope

        # Set of parent nodes on which this node depends
        self.parent_node_set = set()

        # Set of children nodes that depend on this node
        self.children_node_set = set()

        # The cell number when this node is defined
        self.defined_CN = defined_CN

        # The cell number this node is required to have to not be considered as stale dependency
        # The Pair should contain (The required cell number, The ancestor node that was updated)
        self.required_CN_node_pair = (defined_CN, None)

        # The actual id of the object that this node represents.
        self.uid = uid

        # If the node belongs to a set of alias nodes
        self.aliasable = aliasable

        """For example: list is aliasable. Two name can point to the same list.
        Integer is not aliasable because modifying one integer object cannot 
        affect any other integer object that has the same ID"""
        if aliasable:
            # The set of nodes that have the same ID.
            # This should be retrieved from some global dictionary that contains this relation

            #####################INCOMPLETE###########################
            self.alias_set = None
            #####################INCOMPLETE###########################

    def update_CN_node_pair(self, pair, seen=None):
        if seen is None:
            seen = set()
        if self in seen:
            return
        seen.add(self)
        self.required_CN_node_pair = pair
        for n in self.children_node_set:
            n.update_CN_node_pair(pair, seen)
