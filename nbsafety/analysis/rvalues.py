# -*- coding: utf-8 -*-
from __future__ import annotations
import ast


class GetAllRvalNames(ast.NodeVisitor):
    def __init__(self):
        self.name_set = set()

    def __call__(self, node):
        self.visit(node)
        return self.name_set

    def visit_Name(self, node):
        self.name_set.add(node.id)

    def visit_Assign(self, node):
        # skip node.targets
        self.visit(node.value)

    def visit_AugAssign(self, node):
        # skip node.target
        self.visit(node.value)

    def visit_For(self, node):
        # skip node.target (gets bound to node.iter)
        # skip body too -- will have dummy since this visitor works line-by-line
        self.visit(node.iter)

    def visit_Lambda(self, node):
        # remove node.arguments
        self.visit(node.body)
        self.visit(node.args)
        old = self.name_set
        self.name_set = set()
        # throw away anything appearing in lambda body that isn't bound
        self.visit(node.args.args)
        self.visit(node.args.vararg)
        self.visit(node.args.kwonlyargs)
        self.visit(node.args.kwarg)
        self.name_set = old - self.name_set

    def generic_visit(self, node):
        if node is None:
            return
        elif isinstance(node, list):
            for item in node:
                self.visit(item)
        else:
            super().generic_visit(node)

    def visit_arguments(self, node):
        # skip over unbound args
        self.visit(node.defaults)
        self.visit(node.kw_defaults)

    def visit_arg(self, node):
        self.name_set.add(node.arg)


def get_all_rval_names(node: ast.AST):
    return GetAllRvalNames()(node)
