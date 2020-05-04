# -*- coding: utf-8 -*-
from __future__ import annotations
import ast

from .mixins import SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin


class GetStatementLvalRvalSymbols(SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self):
        # TODO: current complete bipartite subgraph will add unncessary edges
        self.lval_symbol_set = set()
        self.rval_symbol_set = set()
        self.gather_rvals = True

    def __call__(self, node):
        self.visit(node)
        return self.lval_symbol_set, self.rval_symbol_set

    @property
    def to_add_set(self):
        if self.gather_rvals:
            return self.rval_symbol_set
        else:
            return self.lval_symbol_set

    def gather_lvals_context(self):
        return self.push_attributes(gather_rvals=False)

    def gather_rvals_context(self):
        return self.push_attributes(gather_rvals=True)

    def visit_Name(self, node):
        self.to_add_set.add(node.id)

    def visit_Subscript(self, node: ast.Subscript):
        self.visit(node.value)
        with self.gather_rvals_context():
            self.visit(node.slice)

    def visit_Assign(self, node):
        with self.gather_lvals_context():
            for target in node.targets:
                self.visit(target)
        self.visit(node.value)

    def visit_AugAssign(self, node):
        with self.gather_lvals_context():
            self.visit(node.target)
        with self.gather_rvals_context():
            self.visit(node.value)

    def visit_For(self, node):
        # skip body -- will have dummy since this visitor works line-by-line
        with self.gather_lvals_context():
            self.visit(node.target)
        with self.gather_rvals_context():
            self.visit(node.iter)

    def visit_FunctionDef(self, node):
        self.lval_symbol_set.add(node.name)
        with self.gather_rvals_context():
            self.visit(node.args)

    def visit_ClassDef(self, node):
        self.lval_symbol_set.add(node.name)
        with self.gather_rvals_context():
            self.visit(node.bases)
            self.visit(node.decorator_list)

    def visit_Keyword(self, node):
        self.visit(node.value)

    def visit_Starred(self, node):
        self.visit(node.value)

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_Lambda(self, node):
        assert self.gather_rvals
        # remove node.arguments
        self.visit(node.body)
        self.visit(node.args)
        with self.push_attributes(rval_symbol_set=set()):
            self.visit(node.args.args)
            self.visit(node.args.vararg)
            self.visit(node.args.kwonlyargs)
            self.visit(node.args.kwarg)
            discard_set = self.rval_symbol_set
        # throw away anything appearing in lambda body that isn't bound
        self.rval_symbol_set -= discard_set

    def visit_With(self, node):
        # skip body
        self.visit(node.items)

    def visit_withitem(self, node):
        with self.gather_lvals_context():
            self.visit(node.optional_vars)
        with self.gather_rvals_context():
            self.visit(node.context_expr)

    def visit_arg(self, node):
        self.to_add_set.add(node.arg)


def get_statement_lval_and_rval_symbols(node: ast.AST):
    return GetStatementLvalRvalSymbols()(node)


class GetAttributeSymbols(ast.NodeVisitor):
    def __init__(self):
        self.symbol_chain = []

    def __call__(self, node: ast.Attribute):
        self.visit(node)
        self.symbol_chain.reverse()
        return self.symbol_chain

    def visit_Call(self, node):
        self.visit(node.func)

    def visit_Attribute(self, node):
        self.symbol_chain.append(node.attr)
        self.visit(node.value)

    def visit_Name(self, node):
        self.symbol_chain.append(node.id)

    def generic_visit(self, node):
        raise ValueError('we should never get here')


def get_attribute_symbols(node: ast.Attribute):
    return GetAttributeSymbols()(node)
