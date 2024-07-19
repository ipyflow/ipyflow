# -*- coding: utf-8 -*-
import ast
from typing import NamedTuple, Optional

from pyccolo._fast.misc_ast_utils import subscript_to_slice  # noqa: F401


class AstRange(NamedTuple):
    lineno: int
    end_lineno: Optional[int]
    col_offset: int
    end_col_offset: Optional[int]

    @classmethod
    def from_ast_node(cls, node: ast.AST) -> "AstRange":
        return cls(
            lineno=node.lineno,  # type: ignore[attr-defined]
            end_lineno=getattr(node, "end_lineno", None),
            col_offset=node.col_offset,  # type: ignore[attr-defined]
            end_col_offset=getattr(node, "end_col_offset", None),
        )
