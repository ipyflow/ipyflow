# -*- coding: utf-8 -*-
import ast

from .ast_utils import remove_subscript
from .scope import Scope
from .unexpected import UNEXPECTED_STATES


class PreCheck(ast.NodeVisitor):
    """
    This function should be called when we want to precheck an ast.Module. For
    each line/block of the cell We first run the check of new assignments, then
    we obtain all the names. In these names, we put the ones that are user
    defined and not in the safe_set into the return check_set for further
    checks.
    """

    def precheck(self, module_node: ast.Module, scope: Scope):
        check_set = set()
        self.safe_set = set()
        self.current_scope = scope
        for node in module_node.body:
            self.visit(node)
            for name in GetAllNames().get_name_set(node):
                if name in self.current_scope.variable_dict and name not in self.safe_set:
                    check_set.add(name)
        return check_set

    # In case of assignment, we put the new assigned variable into a safe_set
    # to indicate that we know for sure it won't have stale dependency.  Note
    # that node.targets might contain multiple ast.Name node in the case of "a
    # = b = 3", so we go through each node in the targets.  Also that target
    # would be an ast.Tuple node in the case of "a,b = 3,4". Thus we need to
    # break the tuple in that case.
    def visit_Assign(self, node: ast.Assign):
        for target_node in node.targets:
            if isinstance(target_node, ast.Tuple):
                for element_node in target_node.elts:
                    element_node = remove_subscript(element_node)
                    if isinstance(element_node, ast.Name):
                        self.safe_set.add(element_node.id)
            target_node = remove_subscript(target_node)
            if isinstance(target_node, ast.Name):
                self.safe_set.add(target_node.id)
            else:
                raise UNEXPECTED_STATES(
                    "Precheck",
                    "visit_Assign",
                    target_node,
                    "Expect to be ast.Tuple or ast.Name",
                )

    # Similar to assignment, but multiple augassignment is not allowed
    def visit_AugAssign(self, node: ast.AugAssign):
        target_node = remove_subscript(node.target)
        if isinstance(target_node, ast.Name):
            self.safe_set.add(target_node.id)
        else:
            raise UNEXPECTED_STATES(
                "Precheck", "visit_AugAssign", target_node, "Expect to be ast.Name"
            )

    # We also put the name of new functions in the safe_set
    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.safe_set.add(node.name)

    def visit_For(self, node: ast.For):
        # Case "for a,b in something: "
        if isinstance(node.target, ast.Tuple):
            for name_node in node.target.elts:
                if isinstance(name_node, ast.Name):
                    self.safe_set.add(name_node.id)
                else:
                    raise UNEXPECTED_STATES(
                        "Precheck", "visit_For", name_node, "Expect to be ast.Name"
                    )
        # case "for a in something"
        elif isinstance(node.target, ast.Name):
            self.safe_set.add(node.target.id)
        else:
            raise UNEXPECTED_STATES(
                "Update", "visit_For", node.target, "Expect to be ast.Tuple or ast.Name"
            )

        # Then we keep doing the visit for the body of the loop.
        for line in node.body:
            self.visit(line)


# Call GetAllNames().get_name_set(ast_tree) to get a set of all names appeared in ast_tree.
# Helper Class
class GetAllNames(ast.NodeVisitor):
    # This function should be called when getting the name set.
    def get_name_set(self, node):
        self.name_set = set()
        self.visit(node)
        return self.name_set

    def visit_Name(self, node: ast.Name):
        self.name_set.add(node.id)

    # We overwrite FunctionDef because we don't need to check names in the body of the definition.
    # Only need to check for default arguments
    def visit_FunctionDef(self, node: ast.FunctionDef):
        if isinstance(node.args, ast.arguments):
            for default_node in node.args.defaults:
                self.visit(default_node)
        else:
            raise UNEXPECTED_STATES(
                "Precheck Helper",
                "visit_FunctionDef",
                node.args,
                "Expect to be ast.arguments",
            )
