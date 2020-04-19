# -*- coding: utf-8 -*-
from __future__ import annotations
import ast
from typing import Any, Dict, List, Optional, Set, Union

from .variable import VariableNode


class Scope(object):
    def __init__(self, counter: List[int], scope_name: str, parent_scope: Optional[Scope] = None):
        # shared counter state from DependencySafety object
        self.counter = counter

        # The actual string name of the Scope
        self.scope_name = scope_name

        # The parent scope of this scope. Set to None if this is the global scope.
        self.parent_scope = parent_scope

        # A "name->scope" dictionary that contains all its children scopes.
        self.children_scope_dict: Dict[str, Scope] = {}

        # If there is a parent scope, then updates its children scope dictionary to add self in.
        if parent_scope:
            parent_scope.children_scope_dict[scope_name] = self

        # A "name->node" dictionary that contains all VariableNode in this scope
        self.variable_dict: Dict[str, VariableNode] = {}

        # The actual f_locals dictionary in the frame that this represents.
        # This will not be initialized untill the actual frame runs.
        # updateDependency.visit_Call will update this.
        self.frame_dict: Optional[Dict[str, Any]] = None

        # The dependency set that will be used when function scope is called.
        # This will remain None until the scope is defined in
        # UpdateDependency.visit_FunctionDef method.  It contains either a
        # string or a integer. String represents an outer scope variable name
        # and integer represents a position of the argument.
        # TODO(smacke): not true? it looks like it can contain VariableNodes?
        # self.call_dependency: Optional[Set[Union[str, int]]] = None
        self.call_dependency: Optional[Set[Union[VariableNode, int]]] = None

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
        self.func_body: Optional[ast.AST] = None

        # This is the arguments of the funciton definition.
        self.func_args: Optional[ast.AST] = None

    # Create a new VariableNode under the current scope and return the node
    def create_node(self, name: str):
        """
        The new created node takes the name passed as its name, the current
        cell number as its defined cell number, this current scope as its
        scope, the id of the object archieved from frame_dict as its id. Lastly
        check if it is aliasable.
        """
        node = VariableNode(
            name,
            self.counter[0],
            self,
            id(self.frame_dict[name]),
            self.is_aliasable(name),
        )

        # update the variable_dict
        self.variable_dict[name] = node
        return node

    # Give a set of parent nodes, update the current node accordingly.
    def update_node(self, node_name: str, dependency_nodes):
        if self.contains_name_current_scope(node_name):
            node = self.get_node_by_name_current_scope(node_name)
        else:
            node = self.create_node(node_name)

        removed_parents = node.parent_node_set - dependency_nodes
        for n in removed_parents:
            n.children_node_set.remove(node)
            node.parent_node_set.remove(n)

        new_parents = dependency_nodes - node.parent_node_set
        for n in new_parents:
            n.children_node_set.add(node)
            node.parent_node_set.add(n)

        node.defined_CN = self.counter[0]
        node.update_CN_node_pair((self.counter[0], node))

    # returns the VariableNode that is represented by the name passed in.
    def get_node_by_name_current_scope(self, name: str):
        return self.variable_dict[name]

    # returns the VariableNode that is represented by the name passed in. Look up all ancestor scopes.
    def get_node_by_name_all_scope(self, name):
        scope = self
        while scope:
            if name in scope.variable_dict:
                return scope.variable_dict[name]
            scope = scope.parent_scope

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
    def contains_name_current_scope(self, name):
        return name in self.variable_dict

    # returns a boolean value if name exists in the scope or all ancestor scopes.
    def contains_name_all_scope(self, name):
        scope = self
        while scope:
            if name in scope.variable_dict:
                return True
            scope = scope.parent_scope
        return False

    def is_my_ancestor_scope(self, ancestor):
        s = self.parent_scope
        while s:
            if s is ancestor:
                return True
            s = s.parent_scope
        return False

    # helper function to check if the object behind the name in the scope is aliasable.
    def is_aliasable(self, name):
        ###### Currently Disabled ########
        return False
        ##################################
        obj = self.frame_dict[name]

        ##################### INCOMPLETE ###########################
        # There should be some check about the object to see that if it is "aliasable"
        if isinstance(obj, int) or isinstance(obj, str):
            aliasable = False
        elif isinstance(obj, list) or isinstance(obj, dict) or isinstance(obj, set):
            aliasable = True
        else:
            aliasable = False
        ##################### INCOMPLETE ###########################

        return aliasable
