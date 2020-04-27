# -*- coding: utf-8 -*-
from __future__ import annotations
import ast
from types import FrameType
from typing import Dict, Iterable, Optional, Set, Tuple, Union

from .ipython_utils import cell_counter
from .data_cell import DataCell


class Scope(object):
    GLOBAL_SCOPE = '<module>'

    def __init__(self, scope_name: str = GLOBAL_SCOPE, parent_scope: Optional[Scope] = None):
        # The actual string name of the Scope
        self.scope_name = scope_name

        # The parent scope of this scope. Set to None if this is the global scope.
        self.parent_scope = parent_scope

        # A "name->scope" dictionary that contains all its children scopes.
        self.children_scope_dict: Dict[str, Scope] = {}

        # If there is a parent scope, then updates its children scope dictionary to add self in.
        if parent_scope:
            parent_scope.children_scope_dict[scope_name] = self

        # A "name->node" dictionary that contains all DataCells in this scope
        self.variable_dict: Dict[str, DataCell] = {}

        # The actual f_locals dictionary in the frame that this represents.
        # This will not be initialized untill the actual frame runs.
        # updateDependency.visit_Call will update this.
        self.frame_dict: Optional[Dict[str, FrameType]] = None

        # The dependency set that will be used when function scope is called.
        # This will remain None until the scope is defined in
        # UpdateDependency.visit_FunctionDef method.  It contains either a
        # string or a integer. String represents an outer scope variable name
        # and integer represents a position of the argument.
        # TODO(smacke): not true? it looks like it can contain VariableNodes?
        # self.call_dependency: Optional[Set[Union[str, int]]] = None
        self.call_dependency: Optional[Set[Union[DataCell, int]]] = None

        # This will remain None until the scope is defined in
        # UpdateDependency.visit_FunctionDef method.  This dictionary is to
        # record dependency of default arguments at the time the function is
        # defined. We don't have the frame_dict of this never ran function and
        # we have to wait until a Call to this function to update the
        # dependencies recorded in this set.
        self.to_update_dependency = None

        # This is the body of a function definition. It won't run until the
        # function is called. Thus, we store it here and when the function is
        # called, we can update the dependency within it.  This will remain
        # None until the scope is defined in the
        # UpdateDependency.visit_FunctionDef.
        self.func_body: Optional[Iterable[ast.stmt]] = None

        # This is the arguments of the funciton definition.
        self.func_args: Optional[ast.arguments] = None

    # Create a new VariableNode under the current scope and return the node
    def create_node(self, name: str):
        """
        The new created node takes the name passed as its name, the current
        cell number as its defined cell number, this current scope as its
        scope, the id of the object archieved from frame_dict as its id. Lastly
        check if it is aliasable.
        """
        node = DataCell(name, self)

        # update the variable_dict
        self.variable_dict[name] = node
        return node

    # Give a set of parent nodes, update the current node accordingly.
    def update_node(self, node_name: str, dependency_nodes):
        if self.contains_name_current_scope(node_name):
            node = self.get_node_by_name_current_scope(node_name)
        else:
            node = self.create_node(node_name)

        node.update_deps(dependency_nodes)

    @property
    def full_path(self) -> Tuple[str, ...]:
        path = (self.scope_name,)
        if self.parent_scope.scope_name == self.GLOBAL_SCOPE:
            return path
        else:
            return self.parent_scope.full_path + path

    # returns the VariableNode that is represented by the name passed in.
    def get_node_by_name_current_scope(self, name: str) -> DataCell:
        return self.variable_dict[name]

    # returns the VariableNode that is represented by the name passed in. Look up all ancestor scopes.
    def get_node_by_name_all_scope(self, name) -> DataCell:
        scope = self
        while scope:
            if name in scope.variable_dict:
                return scope.variable_dict[name]
            scope = scope.parent_scope
        raise ValueError('unable to find node for %s' % name)

    # returns the object that is represented by the name passed in, return none if not existed.
    def get_object_by_name_current_scope(self, name):
        if name in self.frame_dict:
            return self.frame_dict[name]
        return None

    # returns the object that is represented by the name passed in.
    # Look up all ancestor scopes, return none if not existed.
    def get_object_by_name_all_scope(self, name):
        scope = self
        while scope:
            if name in scope.frame_dict:
                return scope.frame_dict[name]
            scope = scope.parent_scope
        return None

    # returns a boolean value that indicates if the name represents a VariableNode in current scope
    def contains_name_current_scope(self, name: str):
        return name in self.variable_dict

    # returns a boolean value if name exists in the scope or all ancestor scopes.
    def contains_name_all_scope(self, name: str):
        scope = self
        while scope:
            if name in scope.variable_dict:
                return True
            scope = scope.parent_scope
        return False

    def is_my_ancestor_scope(self, ancestor: Scope):
        s = self.parent_scope
        while s:
            if s is ancestor:
                return True
            s = s.parent_scope
        return False
