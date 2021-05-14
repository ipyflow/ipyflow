# -*- coding: future_annotations -*-
import ast
import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Set, Tuple

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class ContainsNamedExprVisitor(ast.NodeVisitor):
    def __init__(self):
        self.contains_named_expr = False

    def __call__(self, node: ast.stmt) -> bool:
        if sys.version_info.minor < 8:
            return False
        self.visit(node)
        return self.contains_named_expr

    def visit_NamedExpr(self, node):
        self.contains_named_expr = True

    def generic_visit(self, node: ast.AST):
        if self.contains_named_expr:
            return
        super().generic_visit(node)


def stmt_contains_lval(node: ast.stmt):
    # TODO: expand to method calls, etc.
    simple_contains_lval = isinstance(node, (
        ast.Assign,
        ast.AnnAssign,
        ast.AugAssign,
        ast.ClassDef,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.For,
        ast.Import,
        ast.ImportFrom,
        ast.With,
    ))
    return simple_contains_lval or ContainsNamedExprVisitor()(node)
