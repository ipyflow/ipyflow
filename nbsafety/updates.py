# -*- coding: utf-8 -*-
from __future__ import annotations
import ast
import logging
from typing import cast, Set, TYPE_CHECKING

from .scope import Scope
from .unexpected import UNEXPECTED_STATES
from .variable import VariableNode

if TYPE_CHECKING:
    from .safety import DependencySafety


# Helper function to remove the subscript and return the name node in front
# of the subscript For example: pass in ast.Subscript node "a[3][b][5]"
# will return ast.Name node "a".
def remove_subscript(node: ast.AST):
    while isinstance(node, ast.Subscript):
        node = node.value
    return node


def _compute_function_name(func):
    if not isinstance(func, (ast.Name, ast.Attribute)):
        raise TypeError('can only compute func names for ast.Name or ast.Attribute now')
    if isinstance(func, ast.Name):
        return func.id
    else:
        if isinstance(func.value, ast.Name):
            attrval = cast(ast.Name, func.value)
            return '{}.{}'.format(attrval.id, func.attr)
        elif isinstance(func.value, ast.Str):
            return 'string.{}'.format(func.attr)
        else:
            raise UNEXPECTED_STATES(
                "Update",
                "_compute_function_name",
                func.value,
                "Only support ast.Name and ast.Str for function name computation for now",
            )


class UpdateDependency(ast.NodeVisitor):

    def __init__(self, safety: DependencySafety):
        self.safety = safety
        self.current_scope = safety.global_scope

    def __call__(self, module_node: ast.Module):
        """
        This function should be called when we are in the Update stage. This
        function will init the global_scope's frame_dict if it is never binded.
        Then it will bind the instance attribute current_scope to the scope
        passed in (usually the global_scope). Then it will call super's visit
        function to visit everything in the module_node. Create or update node
        dependencies happened in this cell.
        """
        self.visit(module_node)

    def get_statement_dependency(self, value: ast.AST) -> Set[VariableNode]:
        """
        Helper function that takes in a statement, returns a set that contains
        the dependency node set. Typically on the right side of assignments.
        For example: in a line "a = b + 3", the part "b + 3" will be the value
        argument. The return value will be a set contains the variable node "b"
        that was looked up in all scopes.
        """
        # The check queue starts with the value node passed in
        queue = [value]
        # The return dependency set starts with an empty set
        return_dependency = set()

        while len(queue) > 0:
            node = queue.pop()

            # case "a", add the name to the return set. The base case
            if isinstance(node, ast.Name):
                return_dependency.add(
                    self.current_scope.get_node_by_name_all_scope(node.id)
                )
            # case "a + b", append both sides to the queue
            elif isinstance(node, ast.BinOp):
                queue.append(node.left)
                queue.append(node.right)
            # case "[a,b,c]" or "(a, b, c)", extend all elements of it to the queue
            elif isinstance(node, (ast.List, ast.Tuple)):
                queue.extend(node.elts)
            # case Function Calls
            elif isinstance(node, ast.Call):
                # Should initialize call_dependency if it is called first time
                while isinstance(node.func, ast.Call):
                    node = node.func
                self.visit_Call(node)

                # Get the function id in different cases, if it is a built-in
                # function, we pass in the None's id to look up in
                # dependency_safety.func_id_to_scope_object. Since it can never
                # contain id(None), this will automatically evaluates to false.
                # This behavior ensures that we don't do anything to built-in
                # functions except put all its arguments to check queue. We
                # then obtain everything from the call_dependency and just put
                # them in.
                if isinstance(node.func, (ast.Name, ast.Attribute)):
                    func_id = id(
                        self.current_scope.get_object_by_name_all_scope(_compute_function_name(node.func))
                    )
                elif isinstance(node.func, ast.Subscript):
                    func_id = id(self.get_subscript_object(node.func))
                else:
                    raise UNEXPECTED_STATES(
                        "Update", "get_statement_dependency", node.func,
                        "Only ast.Name, ast.Attribute, and ast.Subscript supported"
                    )

                if func_id not in self.safety.func_id_to_scope_object:
                    queue.extend(node.args)
                    # Should extend keywords too.
                    # Implement later together with the missing part in visit_Call about keywords

                else:
                    func_scope = self.safety.func_id_to_scope_object[func_id]
                    return_dependency.add(
                        func_scope.parent_scope.get_node_by_name_all_scope(
                            func_scope.scope_name
                        )
                    )
                    # In call_dependency, an item could be an integer indicate a position in argument or a node directly
                    for item in func_scope.call_dependency:
                        if isinstance(item, int):
                            if item < len(node.args):
                                queue.append(node.args[item])
                        elif isinstance(item, VariableNode):
                            return_dependency.add(item)
            elif isinstance(node, ast.Subscript):
                queue.append(remove_subscript(node))
            elif isinstance(node, ast.Num):
                continue
            else:
                logging.warning('unsupported node type for node %s', node)
        return return_dependency

    def get_subscript_object(self, node: ast.AST):
        """Helper function to get the object in a ast.Subscript node"""
        slice_list = []
        while isinstance(node, ast.Subscript):
            if not isinstance(node.slice, ast.Index):
                raise UNEXPECTED_STATES(
                    "Update", "visit_Subscript", node.slice, "Expected to be ast.Index"
                )
            if isinstance(node.slice.value, ast.Num):
                slice_list.append(node.slice.value.n)
            elif isinstance(node.slice.value, ast.Name):
                slice_list.append(self.current_scope.frame_dict[node.slice.value.id])
            else:
                raise UNEXPECTED_STATES(
                    "Update",
                    "visit_Subscript",
                    node.slice.value,
                    "Only support ast.Num and ast.Name for now",
                )
            node = node.value

        if isinstance(node, ast.Name):
            name = node.id
            if name not in self.current_scope.frame_dict:
                return None
            ret_object = self.current_scope.frame_dict[name]
            for i in slice_list:
                ret_object = ret_object[i]
            return ret_object
        raise UNEXPECTED_STATES(
            "Update", "visit_Subscript", node, "Only support ast.Name for now"
        )

    # Assignments. Similar to the one in precheck. We go through each target in targets.
    # Then if it is a tuple, we assign each value accordingly.
    def visit_Assign(self, node: ast.Assign):
        """
        Actually we cannot do the below greyed out part because there are different situations:
        a = 1      ----- LHS is ast.Name, RHS is ast.Num(or ast.Name or anything not ast.Tuple)
        a, b = c, 3 ---- LHs is ast.Tuple, RHS is ast.Tuple
        a = (1, 2) ----- LHS is ast.Name, RHS is ast.Tuple
        b, c = a   ----- LHS is ast.Tuple, RHS is ast.Name(or anything not ast.Tuple)
        a = b, c = (e, f) --- a will be assigned to a tuple and b,c are assigned to integers
                            Thus we should not precompute what the RHS dependency is until we enter the loop

        # Since we only have one value node, we can obtain the dependency first for efficiency, so that we don't need to run it for each target
        if self.target isinstance(node.value, ast.Tuple):
            right_side_dependency = [self.get_statement_dependency(elt) for elt in node.value.elts]
        else:
            right_side_dependency = self.get_statement_dependency(node.value)
        """
        for target_node in node.targets:
            if isinstance(target_node, ast.Tuple):
                if not isinstance(node.value, ast.Tuple):
                    raise TypeError('unexpected type for %s' % node.value)
                tuple_value = cast(ast.Tuple, node.value)
                for i in range(len(target_node.elts)):
                    if not isinstance(target_node.elts[i], ast.Name):
                        raise TypeError('unexpected type for %s' % target_node.elts[i])
                    target_i_name = cast(ast.Name, target_node.elts[i])
                    self.current_scope.update_node(
                        target_i_name.id,
                        self.get_statement_dependency(tuple_value.elts[i]),
                    )
            elif isinstance(target_node, ast.Name):
                self.current_scope.update_node(
                    target_node.id, self.get_statement_dependency(node.value)
                )
            elif isinstance(target_node, ast.Subscript):
                name_node = remove_subscript(target_node)
                if not isinstance(name_node, ast.Name):
                    raise UNEXPECTED_STATES(
                        "Update", "visit_Assign", name_node, "Expect to be ast.Name"
                    )
                self.current_scope.update_node(
                    name_node.id, self.get_statement_dependency(node.value)
                )
            else:
                raise UNEXPECTED_STATES(
                    "Update",
                    "visit_Assign",
                    target_node,
                    "Expect to be ast.Tuple, ast.Name or ast.Subscript",
                )

    # For AugAssignments, we first remove subscripts to get the list name. Else,
    # it is just a ast.Name node. We first get its orginal parent_node_set
    # because we want to keep that relation in a AugAssignment. Then we update
    # the node with the new dependencies.
    def visit_AugAssign(self, node: ast.AugAssign):
        if isinstance(node.target, ast.Subscript):
            name_node = remove_subscript(node.target)
        else:
            name_node = node.target

        if isinstance(name_node, ast.Name):
            # If the name is not user-defined, we just ignore
            if not self.current_scope.contains_name_current_scope(name_node.id):
                return
            dependency_nodes = self.get_statement_dependency(node.value)
            dependency_nodes.update(
                self.current_scope.get_node_by_name_current_scope(
                    name_node.id
                ).parent_node_set
            )
            self.current_scope.update_node(name_node.id, dependency_nodes)
        else:
            raise UNEXPECTED_STATES(
                "Update",
                "visit_Assign",
                name_node,
                "Expect to be ast.Name or ast.Subscript",
            )

    # For loops
    def visit_For(self, node: ast.For):
        # Obtain the dependency created by the iter (The "in" part of the for loop)
        iter_dependency = self.get_statement_dependency(node.iter)
        # If it is an unpack situation, we update each variable's dependency
        if isinstance(node.target, ast.Tuple):
            for name_node in node.target.elts:
                if isinstance(name_node, ast.Name):
                    self.current_scope.update_node(name_node.id, iter_dependency)
                else:
                    raise UNEXPECTED_STATES(
                        "Update", "visit_For", name_node, "Expect to be ast.Name"
                    )
        # else if its just one name, we just update that name
        elif isinstance(node.target, ast.Name):
            self.current_scope.update_node(node.target.id, iter_dependency)
        # Otherwise, we reached an unexpected node
        else:
            raise UNEXPECTED_STATES(
                "Update", "visit_For", node.target, "Expect to be ast.Tuple or ast.Name"
            )

        # Then we keep doing the visit for the body of the loop.
        for line in node.body:
            self.visit(line)

    def _make_function_scope_from_name(self, name: str):
        return Scope(name, self.current_scope)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """
        Function definitions.  An argument node contains: args, vararg,
        kwonlyargs, kw_defaults, kwarg, defaults, i.e.:
        ```
        def func(args1, args2, defaults = 3, *vararg, kwonlyargs1, kwonlyargs2, kw_defaults = 3, **kwarg):
            ...
        ```
        """
        # Create a new function scope of its name. Pass in the current scope as
        # its parent scope.
        func_scope = self._make_function_scope_from_name(node.name)

        # Store the scope by its ID in dependency_safety's function attribute
        self.safety.func_id_to_scope_object[
            id(self.current_scope.frame_dict[node.name])
        ] = func_scope

        # Initialize the to_update_dependency dictionary for default arguments
        func_scope.to_update_dependency = dict()

        # We first initialize that this function is dependent on nothing. We
        # will then add in dependencies of default arguments.
        dependency = set()

        # We expect that FunctionDef.args is an ast.arguments, otherwise we
        # raise an unexpected error.
        if not isinstance(node.args, ast.arguments):
            raise UNEXPECTED_STATES(
                "Update", "visit_FunctionDef, node.args", "Expect to be ast.arguments"
            )
        argument_node = node.args

        # For each arg of defaults, we get its dependency then update the
        # function node dependency and the argument dependency within the
        # scope.
        for i in range(len(argument_node.defaults), 0, -1):
            if not isinstance(argument_node.args[-i], ast.arg):
                raise UNEXPECTED_STATES(
                    "Update",
                    "visit_FunctionDef",
                    argument_node.args[-i],
                    "Expect to be ast.arg",
                )

            default_arg_dependency = self.get_statement_dependency(
                argument_node.defaults[-i]
            )
            dependency.update(default_arg_dependency)
            # argument_node.args[-i].arg is the string name of the argument
            func_scope.to_update_dependency[argument_node.args[-i].arg] = set(
                default_arg_dependency
            )

        # Similar to above, but this is for kw_defaults
        for i in range(len(argument_node.kw_defaults)):
            if argument_node.kw_defaults[i]:
                if not isinstance(argument_node.kwonlyargs[i], ast.arg):
                    raise UNEXPECTED_STATES(
                        "Update",
                        "visit_FunctionDef",
                        argument_node.kwonlyargs[i],
                        "Expect to be ast.arg",
                    )

                default_arg_dependency = self.get_statement_dependency(
                    argument_node.kw_defaults[i]
                )
                dependency.update(default_arg_dependency)
                # argument_node.kwonlyargs[i].arg is the string name of the argument
                func_scope.to_update_dependency[argument_node.kwonlyargs[i].arg] = set(
                    default_arg_dependency
                )

        # Record the body for function calls.
        func_scope.func_body = node.body
        func_scope.func_args = node.args

        # Update Node
        self.current_scope.update_node(node.name, dependency)

    def visit_Call(self, node: ast.Call):
        """
        This method is supposed to initialize the call_dependency of a function
        call. Note that we treat a function as two different nodes: a function
        node and a function_call node. Function node is the object of that
        function.  Function_call node is more like a description of what
        dependency you will obtain by calling this function. Thus, this
        function is to help to get the call_dependency first time it ever runs.
        It should detect and return if there is already a call_dependency set.
        """
        if isinstance(node.func, (ast.Name, ast.Attribute)):
            func_name = _compute_function_name(node.func)
            if func_name not in self.current_scope.frame_dict:
                func_id = id(None)
            else:
                func_id = id(self.current_scope.frame_dict[func_name])
        elif isinstance(node.func, ast.Subscript):
            func_id = id(self.get_subscript_object(node.func))
        else:
            raise UNEXPECTED_STATES(
                "Update",
                "visit_Call",
                node.func,
                "Only support ast.Name, ast.Attribute, and ast.Subscript for now",
            )

        # If this is not a user-defined function, we don't need to update the dependency within it
        if func_id not in self.safety.func_id_to_scope_object:
            return

        func_scope = self.safety.func_id_to_scope_object[func_id]

        # If the call_dependency is already bound, no need to run it again
        if func_scope.call_dependency is not None:
            return
        # else we initialize the call dependency to an empty set
        else:
            func_scope.call_dependency = set()

        # Link the frame_dict because this function has already run now
        func_scope.frame_dict = self.safety.frame_dict_by_scope[func_scope.full_path].f_locals

        # Get body part and argument part from the scope object
        func_body = func_scope.func_body
        func_args = func_scope.func_args

        # Save the pointers to original scope and dependencies so that we could
        # do a recursive call to visit and update the relation within the new
        # function scope.
        original_scope = self.current_scope

        # Change instance attribute "new_dependencies" and "current_scope" to be the new ones
        self.current_scope = func_scope

        # The simple arguments list.
        arg_name_list = []
        # Record all simple arguments(including defaults), create nodes for them
        for arg_node in func_args.args:
            if not isinstance(arg_node, ast.arg):
                raise UNEXPECTED_STATES(
                    "Update", "visit_Call", arg_node, "Expect to be ast.arg"
                )
            arg_name_list.append(arg_node.arg)
            self.current_scope.update_node(arg_node.arg, set())
        # Record Vararg
        if func_args.vararg is not None:
            arg_node = func_args.vararg
            if not isinstance(arg_node, ast.arg):
                raise UNEXPECTED_STATES(
                    "Update", "visit_Call", arg_node, "Expect to be ast.arg"
                )
            self.current_scope.update_node(arg_node.arg, set())
        # Record all kwonly arguments(including kw_defaults)
        for arg_node in func_args.kwonlyargs:
            if not isinstance(arg_node, ast.arg):
                raise UNEXPECTED_STATES(
                    "Update", "visit_Call", arg_node, "Expect to be ast.arg"
                )
            self.current_scope.update_node(arg_node.arg, set())
        # Record kwarg
        if func_args.kwarg is not None:
            arg_node = func_args.kwarg
            if not isinstance(arg_node, ast.arg):
                raise UNEXPECTED_STATES(
                    "Update", "visit_Call", arg_node, "Expect to be ast.arg"
                )
            self.current_scope.update_node(arg_node.arg, set())

        # Run each line in the function body
        # funcall_context = UpdateDependenciesFromCallContext(self.safety, self.current_scope)
        for line in func_body:
            if isinstance(line, ast.Return):
                check_set = self.get_statement_dependency(line.value)
                closed_set = set()
                while check_set:
                    check_node = check_set.pop()
                    name, scope = check_node.name, check_node.scope
                    closed_set.add(name)
                    if scope is func_scope:
                        # If it is one of the arguments, put the index in, so that we know which one
                        # of the arguments we need to record. Reduce false positive for those non-used args
                        if name in arg_name_list:
                            func_scope.call_dependency.add(arg_name_list.index(name))
                        elif name == func_args.vararg:
                            func_scope.call_dependency.add(len(arg_name_list))
                        ######There should be a way to get keyword arguments here########
                        # for those still in the scope variables, we keep checking its parents
                        else:
                            check_set.update(
                                [x for x in check_node.parent_node_set if x not in closed_set]
                            )
                    # If it is dependent on something outside of the current scope, then put it in
                    elif func_scope.is_my_ancestor_scope(scope):
                        func_scope.call_dependency.add(check_node)
            elif isinstance(line, ast.FunctionDef):
                self.visit(line)
                # use the funcall_context visitor to traverse the whole function def
                # can be used to get test_func_assign_helper_func2() to pass, but probably
                # need to think of a different / cleaner approach (see note below).
                # func_scope.call_dependency = func_scope.call_dependency | funcall_context.visit(line)
            else:
                self.visit(line)
        # Restore the scope
        self.current_scope = original_scope


# TODO (smacke): this is for a proof of concept in order to get the
# test_func_assign_helper_func2() test to pass, and is mostly
# copy+pasted from visit_Call; will want to delete / rewrite.
# The major challenge is that when inside of a two-level def,
# we do not know at which scope the function was called, so we
# do not know where to look for the captured frame.
class UpdateDependenciesFromCallContext(UpdateDependency):
    """Same as parent class, but traverses FunctionDefs"""

    def visit_FunctionDef(self, node: ast.FunctionDef):
        super().visit_FunctionDef(node)
        func_id = id(self.current_scope.frame_dict[node.name])

        # If this is not a user-defined function, we don't need to update the dependency within it
        if func_id not in self.safety.func_id_to_scope_object:
            return

        func_scope = self.safety.func_id_to_scope_object[func_id]

        # If the call_dependency is already bound, no need to run it again
        if func_scope.call_dependency is not None:
            return
        # else we initialize the call dependency to an empty set
        else:
            func_scope.call_dependency = set()

        # Link the frame_dict because this function has already run now
        try:
            func_scope.frame_dict = self.safety.frame_dict_by_scope[func_scope.full_path].f_locals
        except:
            # TODO: this is a huge hack
            func_scope.frame_dict = self.safety.frame_dict_by_scope[(func_scope.scope_name,)].f_locals

        # Get body part and argument part from the scope object
        func_body = func_scope.func_body
        func_args = func_scope.func_args

        # Save the pointers to original scope and dependencies so that we could
        # do a recursive call to visit and update the relation within the new
        # function scope.
        original_scope = self.current_scope

        # Change instance attribute "new_dependencies" and "current_scope" to be the new ones
        self.current_scope = func_scope

        # The simple arguments list.
        arg_name_list = []
        # Record all simple arguments(including defaults), create nodes for them
        for arg_node in func_args.args:
            if not isinstance(arg_node, ast.arg):
                raise UNEXPECTED_STATES(
                    "Update", "visit_Call", arg_node, "Expect to be ast.arg"
                )
            arg_name_list.append(arg_node.arg)
            self.current_scope.update_node(arg_node.arg, set())
        # Record Vararg
        if func_args.vararg is not None:
            arg_node = func_args.vararg
            if not isinstance(arg_node, ast.arg):
                raise UNEXPECTED_STATES(
                    "Update", "visit_Call", arg_node, "Expect to be ast.arg"
                )
            self.current_scope.update_node(arg_node.arg, set())
        # Record all kwonly arguments(including kw_defaults)
        for arg_node in func_args.kwonlyargs:
            if not isinstance(arg_node, ast.arg):
                raise UNEXPECTED_STATES(
                    "Update", "visit_Call", arg_node, "Expect to be ast.arg"
                )
            self.current_scope.update_node(arg_node.arg, set())
        # Record kwarg
        if func_args.kwarg is not None:
            arg_node = func_args.kwarg
            if not isinstance(arg_node, ast.arg):
                raise UNEXPECTED_STATES(
                    "Update", "visit_Call", arg_node, "Expect to be ast.arg"
                )
            self.current_scope.update_node(arg_node.arg, set())

        # Run each line in the function body
        for line in func_body:
            if isinstance(line, ast.Return):
                check_set = self.get_statement_dependency(line.value)
                closed_set = set()
                while len(check_set) > 0:
                    check_node = check_set.pop()
                    name, scope = check_node.name, check_node.scope
                    closed_set.add(name)
                    if scope is func_scope:
                        # If it is one of the arguments, put the index in, so that we know which one
                        # of the arguments we need to record. Reduce false positive for those non-used args
                        if name in arg_name_list:
                            func_scope.call_dependency.add(arg_name_list.index(name))
                        elif name == func_args.vararg:
                            func_scope.call_dependency.add(len(arg_name_list))
                        ######There should be a way to get keyword arguments here########
                        # for those still in the scope variables, we keep checking its parents
                        else:
                            check_set.update(
                                [x for x in check_node.parent_node_set if x not in closed_set]
                            )
                    # If it is dependent on something outside of the current scope, then put it in
                    elif func_scope.is_my_ancestor_scope(scope):
                        func_scope.call_dependency.add(check_node)
            else:
                self.visit(line)
        # Restore the scope
        self.current_scope = original_scope
        return func_scope.call_dependency

