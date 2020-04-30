# -*- coding: utf-8 -*-
from __future__ import annotations
import ast

from .mixins import SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin


class GetHyperEdgeNames(SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self):
        self.lval_name_set = set()
        self.rval_name_set = set()
        self.gather_rvals = True

    def __call__(self, node):
        self.visit(node)
        return self.lval_name_set, self.rval_name_set

    @property
    def to_add_set(self):
        if self.gather_rvals:
            return self.rval_name_set
        else:
            return self.lval_name_set

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
        self.lval_name_set.add(node.name)
        with self.gather_rvals_context():
            self.visit(node.args)

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
        with self.push_attributes(rval_name_set=set()):
            self.visit(node.args.args)
            self.visit(node.args.vararg)
            self.visit(node.args.kwonlyargs)
            self.visit(node.args.kwarg)
            discard_set = self.rval_name_set
        # throw away anything appearing in lambda body that isn't bound
        self.rval_name_set -= discard_set

    def visit_arg(self, node):
        self.rval_name_set.add(node.arg)


def get_hyperedge_lvals_and_rvals(node: ast.AST):
    return GetHyperEdgeNames()(node)
