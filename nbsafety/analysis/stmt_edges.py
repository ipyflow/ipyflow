# -*- coding: utf-8 -*-
import ast
from typing import TYPE_CHECKING

from .mixins import SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin

if TYPE_CHECKING:
    from typing import List, Set, Union


class GetStatementLvalRvalSymbolRefs(SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self):
        # TODO: current complete bipartite subgraph will add unncessary edges
        # TODO: this is actually pretty important to handle things like a.b.c properly,
        # we should switch to generating a sequence of (lvals, rvals, etc.)
        self.lval_symbol_ref_set: Set[str] = set()
        self.rval_symbol_ref_set: Set[Union[str, int]] = set()
        self.should_overwrite = True
        self.gather_rvals = True

    def __call__(self, node):
        self.visit(node)
        return self.lval_symbol_ref_set, self.rval_symbol_ref_set, self.should_overwrite

    @property
    def to_add_set(self):
        if self.gather_rvals:
            return self.rval_symbol_ref_set
        else:
            return self.lval_symbol_ref_set

    def gather_lvals_context(self):
        return self.push_attributes(gather_rvals=False)

    def gather_rvals_context(self):
        return self.push_attributes(gather_rvals=True)

    def visit_Attribute(self, node):
        # everything here is handled by the attrsub tracer
        return

    def visit_Name(self, node):
        self.to_add_set.add(node.id)

    def visit_Subscript(self, node: ast.Subscript):
        # everything but the name inside the slice is handled by the attrsub tracer
        # if not self.gather_rvals and isinstance(node.slice, ast.Index) and isinstance(node.slice.value, ast.Name):
        #     with self.gather_rvals_context():
        #         self.visit(node.slice.value)
        # TODO: the ast.Name slice dependency turns out to be super hard to refresh, so until we
        #  figure out how to do it in a principled way, we'll just accept potential false negatives
        return

    def visit_Assign(self, node):
        with self.gather_lvals_context():
            for target in node.targets:
                self.visit(target)
        with self.gather_rvals_context():
            self.visit(node.value)

    def visit_AnnAssign(self, node):
        with self.gather_lvals_context():
            self.visit(node.target)
        with self.gather_rvals_context():
            self.visit(node.value)

    def visit_AugAssign(self, node):
        with self.push_attributes(should_overwrite=False):
            with self.gather_lvals_context():
                self.visit(node.target)
            with self.gather_rvals_context():
                self.visit(node.value)

    def visit_Call(self, node):
        with self.gather_rvals_context():
            self.generic_visit(node)

    def visit_For(self, node):
        # skip body -- will have dummy since this visitor works line-by-line
        with self.gather_lvals_context():
            self.visit(node.target)
        with self.gather_rvals_context():
            self.visit(node.iter)

    def visit_FunctionDef(self, node):
        self.lval_symbol_ref_set.add(node.name)
        with self.gather_rvals_context():
            self.visit(node.args)

    def visit_ClassDef(self, node):
        self.lval_symbol_ref_set.add(node.name)
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
        with self.push_attributes(rval_symbol_ref_set=set()):
            self.visit(node.args.args)
            self.visit(node.args.vararg)
            self.visit(node.args.kwonlyargs)
            self.visit(node.args.kwarg)
            discard_set = self.rval_symbol_ref_set
        # throw away anything appearing in lambda body that isn't bound
        self.rval_symbol_ref_set -= discard_set

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


def get_statement_lval_and_rval_symbol_refs(node: ast.AST):
    return GetStatementLvalRvalSymbolRefs()(node)
