# -*- coding: utf-8 -*-
import ast
from typing import cast, TYPE_CHECKING

from nbsafety.analysis.attr_symbols import get_attrsub_symbol_chain
from nbsafety.analysis.mixins import SaveOffAttributesMixin, VisitListsMixin

if TYPE_CHECKING:
    from typing import Sequence, Union


class TiedTuple(tuple):
    """Just a marker class indicating that we should not unpack contents of this tuple"""
    pass


class GetAssignmentLvalRvalSymbolRefs(SaveOffAttributesMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self):
        # TODO: figure out how to give these type annotations
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

    def visit_Num(self, node):
        self.to_add_set.append(None)

    def visit_Str(self, node):
        self.to_add_set.append(None)

    def visit_NameConstant(self, node):
        self.to_add_set.append(None)

    def visit_Tuple(self, node):
        self.visit_List_or_Tuple(node)

    def visit_List(self, node):
        self.visit_List_or_Tuple(node)

    def visit_Dict(self, node):
        temp = self.to_add_set
        self.to_add_set = []
        self.visit(node.keys)
        self.visit(node.values)
        self.to_add_set, temp = temp, self.to_add_set
        self.to_add_set.append(tuple(temp))

    def visit_List_or_Tuple(self, node):
        temp = self.to_add_set
        self.to_add_set = []
        self.visit(node.elts)
        self.to_add_set, temp = temp, self.to_add_set
        self.to_add_set.append(tuple(temp))

    def visit_expr(self, node):
        assert self.gather_rvals
        temp = self.to_add_set
        self.to_add_set = []
        # call super generic_visit since self generic_visit calls visit_expr
        super().generic_visit(node)
        self.to_add_set, temp = temp, self.to_add_set
        self.to_add_set.append(tuple(temp))

    def generic_visit(self, node: 'Union[ast.AST, Sequence[ast.AST]]'):
        # The purpose of this is to make sure we call our visit_expr method if we see an expr
        if isinstance(node, ast.expr):
            self.visit_expr(node)
        else:
            super().generic_visit(node)

    def visit_Assign(self, node):
        with self.gather_lvals_context():
            for target in node.targets:
                target_lval_symbols = []
                with self.push_attributes(lval_symbols=target_lval_symbols):
                    self.visit(target)
                if isinstance(target, (ast.List, ast.Tuple)):
                    # not strictly necessary since we are robust to this later,
                    # but helps avoid unncessary double nesting, e.g., ((a, b, c),)
                    assert len(target_lval_symbols) == 1
                    assert isinstance(target_lval_symbols[0], tuple)
                    self.lval_symbols.append(target_lval_symbols[0])
                else:
                    self.lval_symbols.append(tuple(target_lval_symbols))
        with self.gather_rvals_context():
            self.visit(node.value)

    def visit_Call(self, node):
        extra_to_add = []
        if isinstance(node.func, (ast.Attribute, ast.Subscript)):
            extra_to_add.append(get_attrsub_symbol_chain(node))
            to_visit = [node.args, node.keywords]
        else:
            to_visit = node
        temp = self.to_add_set
        self.to_add_set = []
        self.generic_visit(to_visit)
        self.to_add_set, temp = temp, self.to_add_set
        temp = TiedTuple(set(_flatten(temp)) | set(_flatten(extra_to_add)))
        self.to_add_set.append(temp)

    def visit_Attribute_or_Subscript(self, node):
        # TODO: we'll ignore args inside of inner calls, e.g. f.g(x, y).h
        self.to_add_set.append(get_attrsub_symbol_chain(node))

    def visit_Attribute(self, node):
        self.visit_Attribute_or_Subscript(node)

    def visit_Subscript(self, node):
        self.visit_Attribute_or_Subscript(node)

    def visit_Keyword(self, node):
        self.visit(node.value)

    def visit_Starred(self, node):
        self.visit(node.value)

    def visit_Lambda(self, node):
        assert self.gather_rvals
        # remove node.arguments
        self.visit(node.body)
        self.visit(node.args)
        with self.push_attributes(rval_symbols=[]):
            self.visit(node.args.args)
            self.visit(node.args.vararg)
            self.visit(node.args.kwonlyargs)
            self.visit(node.args.kwarg)
            discard_set = set(self.rval_symbols)
        # throw away anything appearing in lambda body that isn't bound
        self.rval_symbols = list(set(self.rval_symbols) - discard_set)

    def visit_GeneratorExp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_DictComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_ListComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_SetComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(self, node):
        assert self.gather_rvals
        with self.push_attributes(rval_symbols=[]):
            self.visit(node.generators)
            discard_set = set(self.rval_symbols)
        self.rval_symbols = list(set(self.rval_symbols) - discard_set)
        # TODO: actually append the symbols that appear in the comprehension
        self.rval_symbols.append(TiedTuple())

    def visit_arg(self, node):
        self.to_add_set.append(node.arg)


def _flatten(vals):
    for v in vals:
        if isinstance(v, tuple):
            yield from _flatten(v)
        else:
            yield v


def _edges(lvals, rvals):
    if isinstance(lvals, tuple) and isinstance(rvals, tuple):
        yield from _edges_from_tuples(lvals, rvals)
    elif isinstance(lvals, tuple):
        # TODO: yield edges with subscript symbols
        for left in _flatten(lvals):
            yield left, rvals
    elif isinstance(rvals, tuple):
        # TODO: yield edges with subscript symbols
        for right in _flatten(rvals):
            yield lvals, right
    else:
        yield lvals, rvals


def _edges_from_tuples(lvals, rvals):
    if isinstance(rvals, TiedTuple):
        for lval in lvals:
            yield from _edges(lval, rvals)
    elif len(lvals) == len(rvals):
        for left, right in zip(lvals, rvals):
            yield from _edges(left, right)
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
        edges_for_lval = list(_edges(lval_list, tuple(visitor.rval_symbols)))
        if len(edges_for_lval) == 0:
            for lval in lval_list:
                yield lval, None
        else:
            yield from edges_for_lval
