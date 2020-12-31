# -*- coding: utf-8 -*-
import ast
import logging
from typing import cast, TYPE_CHECKING

from nbsafety.analysis.attr_symbols import get_attrsub_symbol_chain, AttrSubSymbolChain, CallPoint
from nbsafety.analysis.mixins import SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin

if TYPE_CHECKING:
    from typing import List, Optional, Set, Tuple, Union
    from ..types import SymbolRef

logger = logging.getLogger(__name__)


# TODO: have the logger warnings additionally raise exceptions for tests
class ComputeLiveSymbolRefs(SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self, init_killed: 'Optional[Set[str]]' = None):
        self.live: 'Set[SymbolRef]' = set()
        if init_killed is None:
            self.dead: 'Set[SymbolRef]' = set()
        else:
            self.dead = cast('Set[SymbolRef]', init_killed)
        self.in_kill_context = False
        self.inside_attrsub = False
        self.skip_simple_names = False

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
        for node in module_node.body:
            self.visit(node)
        return self.live, self.dead

    def kill_context(self):
        return self.push_attributes(in_kill_context=True)

    def attrsub_context(self, inside=True):
        return self.push_attributes(inside_attrsub=inside, skip_simple_names=inside)

    def args_context(self):
        return self.push_attributes(skip_simple_names=False)

    def _add_attrsub_to_live_if_eligible(self, ref: 'AttrSubSymbolChain'):
        if ref in self.dead:
            return
        if len(ref.symbols) == 0:
            # can happen if user made syntax error like [1, 2, 3][4, 5, 6] (e.g. forgot comma)
            return
        leading_symbol = ref.symbols[0]
        if isinstance(leading_symbol, str) and leading_symbol in self.dead:
            return
        if isinstance(leading_symbol, CallPoint) and leading_symbol.symbol in self.dead:
            return
        self.live.add(ref)

    # the idea behind this one is that we don't treat a symbol as dead
    # if it is used on the RHS of an assignment
    def visit_Assign_impl(self, targets, value, aug_assign_target=None):
        this_assign_live = set()
        this_assign_dead = set()
        with self.push_attributes(live=this_assign_live):
            self.visit(value)
            if aug_assign_target is not None:
                self.visit(aug_assign_target)
        with self.push_attributes(dead=this_assign_dead):
            with self.kill_context():
                for target in targets:
                    self.visit_Assign_target(target)
        this_assign_dead -= this_assign_live
        self.live |= this_assign_live
        self.dead |= this_assign_dead

    def visit_NamedExpr(self, node):
        self.visit_Assign_impl([node.target], node.value)

    def visit_Assign(self, node: ast.Assign):
        self.visit_Assign_impl(node.targets, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        self.visit_Assign_impl([node.target], node.value)

    def visit_AugAssign(self, node: ast.AugAssign):
        self.visit_Assign_impl([], node.value, aug_assign_target=node.target)

    def visit_Assign_target(
            self, target_node: 'Union[ast.Attribute, ast.Name, ast.Subscript, ast.Tuple, ast.List, ast.expr]'
    ):
        if isinstance(target_node, ast.Name):
            self.dead.add(target_node.id)
        elif isinstance(target_node, (ast.Attribute, ast.Subscript)):
            self.dead.add(get_attrsub_symbol_chain(target_node))
        elif isinstance(target_node, (ast.Tuple, ast.List)):
            for elt in target_node.elts:
                self.visit_Assign_target(elt)
        else:
            logger.warning('unsupported type for node %s' % target_node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.generic_visit(node.args.defaults)
        self.generic_visit(node.decorator_list)
        self.dead.add(node.name)

    def visit_Name(self, node):
        if self.in_kill_context:
            self.dead.add(node.id)
        elif not self.skip_simple_names and node.id not in self.dead:
            self.live.add(node.id)

    def visit_Tuple_or_List(self, node):
        for elt in node.elts:
            self.visit(elt)

    def visit_List(self, node):
        self.visit_Tuple_or_List(node)

    def visit_Tuple(self, node):
        self.visit_Tuple_or_List(node)

    def visit_For(self, node: ast.For):
        # Case "for a,b in something: "
        self.visit(node.iter)
        with self.kill_context():
            self.visit(node.target)

        for line in node.body:
            self.visit(line)

    def visit_ClassDef(self, node: ast.ClassDef):
        self.generic_visit(node.bases)
        self.generic_visit(node.decorator_list)

    def visit_Call(self, node: ast.Call):
        with self.args_context():
            self.generic_visit(node.args)
            for kwarg in node.keywords:
                self.visit(kwarg.value)
        self._add_attrsub_to_live_if_eligible(get_attrsub_symbol_chain(node))
        if isinstance(node.func, (ast.Attribute, ast.Subscript)):
            with self.attrsub_context():
                self.visit(node.func)
        else:
            self.visit(node.func)

    def visit_Attribute(self, node: ast.Attribute):
        if not self.inside_attrsub:
            self._add_attrsub_to_live_if_eligible(get_attrsub_symbol_chain(node))
        with self.attrsub_context():
            self.visit(node.value)

    def visit_Subscript(self, node: ast.Subscript):
        if not self.inside_attrsub:
            self._add_attrsub_to_live_if_eligible(get_attrsub_symbol_chain(node))
        with self.attrsub_context():
            self.visit(node.value)
        with self.attrsub_context(inside=False):
            self.visit(node.slice)

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
        for gen in node.generators:
            self.visit(gen.iter)
            with self.kill_context():
                self.visit(gen.target)

    def visit_Lambda(self, node):
        with self.kill_context():
            self.visit(node.args)

    def visit_arg(self, node):
        if self.in_kill_context:
            self.dead.add(node.arg)
        elif not self.skip_simple_names and node.arg not in self.dead:
            self.live.add(node.arg)


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
