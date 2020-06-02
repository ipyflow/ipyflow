# -*- coding: utf-8 -*-
import ast
import logging
from typing import TYPE_CHECKING

from .attr_symbols import get_attrsub_symbol_chain, AttrSubSymbolChain
from .mixins import SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin
from .symbol_ref import SymbolRef

if TYPE_CHECKING:
    from typing import List, Set, Union
    from ..safety import DependencySafety

logger = logging.getLogger(__name__)


# TODO: have the logger warnings additionally raise exceptions for tests
class ComputeLiveSymbolRefs(ast.NodeVisitor):

    def __init__(self, safety: 'DependencySafety'):
        self.safety = safety
        self.killed: Set[Union[str, AttrSubSymbolChain]] = set()

    def __call__(self, module_node: ast.Module):
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
            for ref in _get_all_symbol_refs(node):
                if ref.symbol in self.killed:
                    continue
                # TODO: check for all subchains in the safe set, not just the first symbol
                if isinstance(ref.symbol, AttrSubSymbolChain):
                    leading_symbol = ref.symbol.symbols[0]
                    if isinstance(leading_symbol, str) and leading_symbol in self.killed:
                        continue
                check_set.add(ref)
        # print(self.safe_set)
        # print(check_set)
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
                        self.killed.add(element_node.id)
            else:
                self.visit_Assign_or_AugAssign_target(target_node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        self.visit_Assign_or_AugAssign_target(node.target)

    def visit_AugAssign(self, node: ast.AugAssign):
        self.visit_Assign_or_AugAssign_target(node.target)

    def visit_Assign_or_AugAssign_target(self, target_node: 'Union[ast.Attribute, ast.Name, ast.Subscript, ast.expr]'):
        if isinstance(target_node, ast.Name):
            self.killed.add(target_node.id)
        elif isinstance(target_node, (ast.Attribute, ast.Subscript)):
            self.killed.add(get_attrsub_symbol_chain(target_node))
        else:
            logger.warning('unsupported type for node %s' % target_node)

    # We also put the name of new functions in the safe_set
    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.killed.add(node.name)

    def visit_For(self, node: ast.For):
        # Case "for a,b in something: "
        if isinstance(node.target, ast.Tuple):
            for name_node in node.target.elts:
                if isinstance(name_node, ast.Name):
                    self.killed.add(name_node.id)
                else:
                    logger.warning('unsupported type for node %s' % name_node)
        # case "for a in something"
        elif isinstance(node.target, ast.Name):
            self.killed.add(node.target.id)
        else:
            logger.warning('unsupported type for node %s' % node.target)

        # Then we keep doing the visit for the body of the loop.
        for line in node.body:
            self.visit(line)


# Call GetAllNames()(ast_tree) to get a set of all names appeared in ast_tree.
# Helper Class
class GetAllSymbolRefs(SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self):
        self.ref_set: Set[SymbolRef] = set()
        self.inside_attrsub = False
        self.skip_simple_names = False

    def __call__(self, node: ast.AST):
        self.visit(node)
        return self.ref_set

    def attrsub_context(self, inside=True):
        return self.push_attributes(inside_attrsub=inside, skip_simple_names=inside)

    def args_context(self):
        return self.push_attributes(skip_simple_names=False)

    def visit_Name(self, node: ast.Name):
        if not self.skip_simple_names:
            self.ref_set.add(SymbolRef(node.id))

    # We overwrite FunctionDef because we don't need to check names in the body of the definition.
    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.visit(node.args)

    def visit_ClassDef(self, node: ast.ClassDef):
        self.generic_visit(node.bases)
        self.generic_visit(node.decorator_list)

    def visit_Call(self, node: ast.Call):
        with self.args_context():
            self.generic_visit(node.args)
            for kwarg in node.keywords:
                self.visit(kwarg.value)
        if isinstance(node.func, (ast.Attribute, ast.Subscript)):
            self.ref_set.add(SymbolRef(get_attrsub_symbol_chain(node)))
            with self.attrsub_context():
                self.visit(node.func)
        else:
            self.visit(node.func)

    def visit_Attribute(self, node: ast.Attribute):
        if not self.inside_attrsub:
            self.ref_set.add(SymbolRef(get_attrsub_symbol_chain(node)))
        with self.attrsub_context():
            self.visit(node.value)

    def visit_Subscript(self, node: ast.Subscript):
        if not self.inside_attrsub:
            self.ref_set.add(SymbolRef(get_attrsub_symbol_chain(node)))
        with self.attrsub_context():
            self.visit(node.value)
        with self.attrsub_context(inside=False):
            self.visit(node.slice)


def _get_all_symbol_refs(node: ast.AST):
    return GetAllSymbolRefs()(node)
