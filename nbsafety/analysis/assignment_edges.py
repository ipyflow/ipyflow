# -*- coding: utf-8 -*-
import ast
from typing import cast, TYPE_CHECKING

from .mixins import SaveOffAttributesMixin, VisitListsMixin

if TYPE_CHECKING:
    from typing import Union


class GetAssignmentLvalRvalSymbolRefs(SaveOffAttributesMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self):
        self.lval_symbols = []
        self.rval_symbols = []
        self.gather_rvals = True

    def __call__(self, node: ast.Assign):
        self.visit(node)

    def gather_lvals_context(self):
        return self.push_attributes(gather_rvals=False)

    def gather_rvals_context(self):
        return self.push_attributes(gather_rvals=True)

    @property
    def to_add_set(self):
        if self.gather_rvals:
            return self.rval_symbols
        else:
            return self.lval_symbols

    @to_add_set.setter
    def to_add_set(self, val):
        if self.gather_rvals:
            self.rval_symbols = val
        else:
            self.lval_symbols = val

    def visit_Name(self, node):
        self.to_add_set.append(node.id)

    def visit_Tuple(self, node):
        self.visit_List_or_Tuple(node)

    def visit_List(self, node):
        self.visit_List_or_Tuple(node)

    def visit_List_or_Tuple(self, node):
        inner_symbols = []
        with self.push_attributes(to_add_set=inner_symbols):
            self.visit(node.elts)
        self.to_add_set.append(inner_symbols)

    def visit_Assign(self, node):
        with self.gather_lvals_context():
            for target in node.targets:
                target_lval_symbols = []
                with self.push_attributes(lval_symbols=target_lval_symbols):
                    self.visit(target)
                self.lval_symbols.append(target_lval_symbols)
        with self.gather_rvals_context():
            self.visit(node.value)


def _flatten(vals):
    for v in vals:
        if isinstance(v, list):
            yield from _flatten(v)
        else:
            yield v


def _edges(lvals, rvals):
    if isinstance(lvals, list) and isinstance(rvals, list):
        yield from _edges_from_lists(lvals, rvals)
    elif isinstance(lvals, list):
        # TODO: yield edges with subscript symbols
        for l in _flatten(lvals):
            yield l, rvals
    elif isinstance(rvals, list):
        # TODO: yield edges with subscript symbols
        for r in _flatten(rvals):
            yield lvals, r
    else:
        yield lvals, rvals


def _edges_from_lists(lvals, rvals):
    if len(lvals) == len(rvals):
        for l, r in zip(lvals, rvals):
            yield from _edges(l, r)
    elif len(lvals) == 1:
        yield from _edges(lvals[0], rvals)
    elif len(rvals) == 1:
        yield from _edges(lvals, rvals[0])
    else:
        raise ValueError('Incompatible lists: %s, %s' % (lvals, rvals))


def get_assignment_lval_and_rval_symbol_refs(node: 'Union[str, ast.Assign]'):
    if isinstance(node, str):
        node = cast(ast.Assign, ast.parse(node).body[0])
    assert isinstance(node, ast.Assign)
    visitor = GetAssignmentLvalRvalSymbolRefs()
    visitor(node)
    for lval_list in visitor.lval_symbols:
        yield from _edges(lval_list, visitor.rval_symbols)
