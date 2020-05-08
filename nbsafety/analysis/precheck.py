# -*- coding: utf-8 -*-
import ast
import logging
from typing import TYPE_CHECKING

from .attr_symbols import get_attribute_symbol_chain
from .mixins import SkipUnboundArgsMixin, VisitListsMixin

if TYPE_CHECKING:
    from .attr_symbols import AttributeSymbolChain
    from typing import KeysView, List, Set, Union

logger = logging.getLogger(__name__)


# TODO: have the logger warnings additionally raise exceptions for tests
class PreCheck(ast.NodeVisitor):

    def __init__(self):
        self.safe_set: Set[Union[str, AttributeSymbolChain]] = set()

    def __call__(self, module_node: ast.Module, name_set: 'KeysView[str]'):
        """
        This function should be called when we want to precheck an ast.Module. For
        each line/block of the cell we first run the check of new assignments, then
        we obtain all the names. In these names, we put the ones that are user
        defined and not in the safe_set into the return check_set for further
        checks.
        """
        check_set = set()
        for node in module_node.body:
            self.visit(node)
            for name in get_all_names(node):
                if name not in self.safe_set:
                    check_set.add(name)
        return check_set

    # In case of assignment, we put the new assigned variable into a safe_set
    # to indicate that we know for sure it won't have stale dependency.  Note
    # that node.targets might contain multiple ast.Name node in the case of
    # "a = b = 3", so we go through each node in the targets.  Also note that
    # `target` would be an ast.Tuple node in the case of "a,b = 3,4". Thus
    # we need to break the tuple in that case.
    def visit_Assign(self, node: ast.Assign):
        for target_node in node.targets:
            if isinstance(target_node, ast.Tuple):
                for element_node in target_node.elts:
                    if isinstance(element_node, ast.Name):
                        self.safe_set.add(element_node.id)
            else:
                self.visit_Assign_or_AugAssign_target(target_node)

    def visit_AugAssign(self, node: ast.AugAssign):
        self.visit_Assign_or_AugAssign_target(node.target)

    def visit_Assign_or_AugAssign_target(self, target_node: 'Union[ast.Attribute, ast.Name, ast.Subscript]'):
        ignore_node_types = (ast.Subscript,)
        if isinstance(target_node, ignore_node_types):
            return
        elif isinstance(target_node, ast.Name):
            self.safe_set.add(target_node.id)
        elif isinstance(target_node, ast.Attribute):
            self.safe_set.add(get_attribute_symbol_chain(target_node))
        else:
            logger.warning('unsupported type for node %s' % target_node)

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
                    logger.warning('unsupported type for node %s' % name_node)
        # case "for a in something"
        elif isinstance(node.target, ast.Name):
            self.safe_set.add(node.target.id)
        else:
            logger.warning('unsupported type for node %s' % node.target)

        # Then we keep doing the visit for the body of the loop.
        for line in node.body:
            self.visit(line)


def precheck(code: 'Union[ast.Module, str]', name_set: 'KeysView[str]'):
    if isinstance(code, str):
        code = ast.parse(code)
    return PreCheck()(code, name_set)


# Call GetAllNames()(ast_tree) to get a set of all names appeared in ast_tree.
# Helper Class
class GetAllNames(SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self):
        self.name_set: Set[Union[str, AttributeSymbolChain]] = set()

    def __call__(self, node: ast.AST):
        self.visit(node)
        return self.name_set

    def visit_Name(self, node: ast.Name):
        self.name_set.add(node.id)

    # We overwrite FunctionDef because we don't need to check names in the body of the definition.
    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.visit(node.args)

    def visit_ClassDef(self, node: ast.ClassDef):
        self.generic_visit(node.bases)
        self.generic_visit(node.decorator_list)

    def visit_Attribute(self, node: ast.Attribute):
        self.name_set.add(get_attribute_symbol_chain(node))


def get_all_names(node: ast.AST):
    return GetAllNames()(node)
