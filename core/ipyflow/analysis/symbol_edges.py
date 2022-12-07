# -*- coding: utf-8 -*-
import ast
import logging
from typing import List, Sequence, Tuple, Union

from ipyflow.analysis.mixins import (
    SaveOffAttributesMixin,
    SkipUnboundArgsMixin,
    VisitListsMixin,
)

logger = logging.getLogger(__name__)


class GetSymbolEdges(
    SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor
):
    def __init__(self) -> None:
        self.edges: List[Tuple[Union[str, ast.AST], ast.AST]] = []

    def __call__(self, node: ast.AST) -> List[Tuple[Union[str, ast.AST], ast.AST]]:
        self.visit(node)
        # need to reverse in order to handle nested edges first,
        # since these need to have symbols in place for e.g. nested NamedExpr's
        self.edges.reverse()
        return self.edges

    def visit_expr(self, node):
        # python <= 3.7 doesn't support isinstance(obj, None)
        if hasattr(ast, "NamedExpr") and isinstance(node, getattr(ast, "NamedExpr")):
            self.visit_NamedExpr(node)
        else:
            super().generic_visit(node)

    def visit_NamedExpr(self, node):
        self.edges.append((node.target, node.value))
        self.visit(node.value)

    def generic_visit(self, node: Union[ast.AST, Sequence[ast.AST]]):
        # The purpose of this is to make sure we call our visit_expr method if we see an expr
        if node is None:
            return
        elif isinstance(node, ast.expr):
            self.visit_expr(node)
        else:
            super().generic_visit(node)

    def visit_AugAssign_or_AnnAssign(self, node):
        self.edges.append((node.target, node.value))
        self.visit(node.value)

    def visit_AnnAssign(self, node):
        self.visit_AugAssign_or_AnnAssign(node)

    def visit_AugAssign(self, node):
        self.visit_AugAssign_or_AnnAssign(node)

    def visit_For(self, node):
        # skip body -- will have dummy since this visitor works line-by-line
        self.edges.append((node.target, node.iter))
        self.visit(node.iter)

    def visit_If(self, node):
        # skip body here too
        self.visit(node.test)

    def visit_FunctionDef_or_AsyncFunctionDef(self, node):
        self.edges.append((node.name, node))
        self.visit(node.args)
        self.visit(node.decorator_list)

    def visit_FunctionDef(self, node):
        self.visit_FunctionDef_or_AsyncFunctionDef(node)

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef_or_AsyncFunctionDef(node)

    def visit_ClassDef(self, node):
        self.edges.append((node.name, node))
        self.visit(node.bases)
        self.visit(node.decorator_list)

    def visit_With(self, node):
        # skip body
        self.visit(node.items)

    def visit_withitem(self, node):
        aliases = node.optional_vars
        if aliases is not None:
            # TODO: ideally we should unpack from the namespace
            if isinstance(aliases, list):
                for alias in aliases:
                    self.edges.append((alias, node.context_expr))
            else:
                self.edges.append((aliases, node.context_expr))

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        # TODO: this needs test coverage I think
        if node.name is not None and node.type is not None:
            self.edges.append((node.name, node.type))

    def visit_Import(self, node: ast.Import):
        self.visit_Import_or_ImportFrom(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        self.visit_Import_or_ImportFrom(node)

    def visit_Import_or_ImportFrom(self, node: Union[ast.Import, ast.ImportFrom]):
        for name in node.names:
            if name.asname is None:
                self.edges.append((name.name, name))
            else:
                self.edges.append((name.asname, name))


def get_symbol_edges(
    node: Union[str, ast.AST]
) -> List[Tuple[Union[str, ast.AST], ast.AST]]:
    if isinstance(node, str):
        node = ast.parse(node).body[0]
    return GetSymbolEdges()(node)
