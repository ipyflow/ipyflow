# -*- coding: utf-8 -*-
import ast
import logging
from typing import cast, TYPE_CHECKING

from nbsafety.analysis.attr_symbols import get_attrsub_symbol_chain, AttrSubSymbolChain, CallPoint
from nbsafety.analysis.mixins import SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin

if TYPE_CHECKING:
    from typing import List, Optional, Set, Tuple, Union
    from ..types import SymbolRef
    Killable = Union[str, AttrSubSymbolChain]

logger = logging.getLogger(__name__)


# TODO: have the logger warnings additionally raise exceptions for tests
class ComputeLiveSymbolRefs(SaveOffAttributesMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self, init_killed: 'Optional[Set[str]]' = None):
        if init_killed is None:
            self.dead: 'Set[Killable]' = set()
        else:
            self.dead = cast('Set[Killable]', init_killed)
        self.in_kill_context = False

    def __call__(self, module_node: ast.Module):
        """
        This function should be called when we want to do a liveness check on a
        cell's corresponding ast.Module. For each line/block of the cell we
        first run the check of new assignments, then we obtain all the names.
        In these names, we put the ones that are user defined and not in the
        killed set into the return check_set for further checks.
        """
        # TODO: this will break if we ref a variable in a loop before killing it in the
        #   same loop, since we will add everything on the LHS of an assignment to the killed
        #   set before checking the loop body for live variables
        live = set()
        for node in module_node.body:
            self.visit(node)
            for ref in _get_all_symbol_refs(node):
                if ref in self.dead:
                    continue
                # TODO: check for all subchains in the safe set, not just the first symbol
                if isinstance(ref, AttrSubSymbolChain):
                    if len(ref.symbols) == 0:
                        # can happen if user made syntax error like [1, 2, 3][4, 5, 6] (e.g. forgot comma)
                        continue
                    leading_symbol = ref.symbols[0]
                    if isinstance(leading_symbol, str) and leading_symbol in self.dead:
                        continue
                    if isinstance(leading_symbol, CallPoint) and leading_symbol.symbol in self.dead:
                        continue
                live.add(ref)
        # print(self.safe_set)
        # print(check_set)
        return live, self.dead

    def kill_context(self):
        return self.push_attributes(in_kill_context=True)

    # In case of assignment, we put the new assigned variable into a safe_set
    # to indicate that we know for sure it won't have stale dependency.  Note
    # that node.targets might contain multiple ast.Name node in the case of
    # "a = b = 3", so we go through each node in the targets.  Also note that
    # `target` would be an ast.Tuple node in the case of "a,b = 3,4". Thus
    # we need to break the tuple in that case.
    def visit_Assign(self, node: ast.Assign):
        with self.kill_context():
            self.generic_visit(node.targets)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        self.visit_Assign_or_AugAssign_target(node.target)
        self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign):
        self.visit_Assign_or_AugAssign_target(node.target)
        self.visit(node.value)

    def visit_Assign_or_AugAssign_target(self, target_node: 'Union[ast.Attribute, ast.Name, ast.Subscript, ast.expr]'):
        if isinstance(target_node, ast.Name):
            self.dead.add(target_node.id)
        elif isinstance(target_node, (ast.Attribute, ast.Subscript)):
            self.dead.add(get_attrsub_symbol_chain(target_node))
        else:
            logger.warning('unsupported type for node %s' % target_node)

    # We also put the name of new functions in the safe_set
    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.dead.add(node.name)
        # with self.kill_context():
        #     self.visit(node.args)

    def visit_Name(self, node):
        if self.in_kill_context:
            self.dead.add(node.id)

    def visit_Tuple_or_List(self, node):
        for elt in node.elts:
            self.visit(elt)

    def visit_List(self, node):
        self.visit_Tuple_or_List(node)

    def visit_Tuple(self, node):
        self.visit_Tuple_or_List(node)

    def visit_For(self, node: ast.For):
        # Case "for a,b in something: "
        with self.kill_context():
            self.visit(node.target)

        # Then we keep doing the visit for the body of the loop.
        for line in node.body:
            self.visit(line)

    def visit_GeneratorExp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_DictComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_ListComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_SetComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(self, node):
        # TODO: as w/ for loop, this will have false positives on later live references
        with self.kill_context():
            for gen in node.generators:
                self.visit(gen.target)

    def visit_Lambda(self, node):
        with self.kill_context():
            self.visit(node.args)

    def visit_arg(self, node):
        if self.in_kill_context:
            self.dead.add(node.arg)


def compute_live_dead_symbol_refs(
        code: 'Union[ast.Module, List[ast.stmt], str]',
        init_killed: 'Optional[Set[str]]' = None
) -> 'Tuple[Set[SymbolRef], Set[SymbolRef]]':
    if init_killed is None:
        init_killed = set()
    if isinstance(code, str):
        code = ast.parse(code)
    elif isinstance(code, list):
        code = ast.Module(body=code)
    return ComputeLiveSymbolRefs(init_killed)(code)


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
            self.ref_set.add(node.id)

    # We overwrite FunctionDef because we don't need to check names in the body of the definition.
    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.generic_visit(node.args.defaults)

    def visit_ClassDef(self, node: ast.ClassDef):
        self.generic_visit(node.bases)
        self.generic_visit(node.decorator_list)

    def visit_Call(self, node: ast.Call):
        with self.args_context():
            self.generic_visit(node.args)
            for kwarg in node.keywords:
                self.visit(kwarg.value)
        self.ref_set.add(get_attrsub_symbol_chain(node))
        if isinstance(node.func, (ast.Attribute, ast.Subscript)):
            with self.attrsub_context():
                self.visit(node.func)
        else:
            self.visit(node.func)

    def visit_Attribute(self, node: ast.Attribute):
        if not self.inside_attrsub:
            self.ref_set.add(get_attrsub_symbol_chain(node))
        with self.attrsub_context():
            self.visit(node.value)

    def visit_Subscript(self, node: ast.Subscript):
        if not self.inside_attrsub:
            self.ref_set.add(get_attrsub_symbol_chain(node))
        with self.attrsub_context():
            self.visit(node.value)
        with self.attrsub_context(inside=False):
            self.visit(node.slice)


def _get_all_symbol_refs(node: ast.AST):
    return GetAllSymbolRefs()(node)
