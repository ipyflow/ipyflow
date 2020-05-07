# -*- coding: utf-8 -*-
import ast
from typing import TYPE_CHECKING
from collections import deque

if TYPE_CHECKING:
    from typing import Deque, Optional, Tuple, Union


def compute_lineno_to_stmt_mapping(node: 'Union[str, ast.AST]'):
    if isinstance(node, str):
        node = ast.parse(node)
    q: Deque[Tuple[ast.AST, Optional[ast.AST]]] = deque([(node, None)])
    line_to_stmt_map = {}
    while len(q) > 0:
        node, stmt_node = q.pop()
        if isinstance(node, ast.stmt):
            stmt_node = node
        if stmt_node is not None and hasattr(node, 'lineno'):
            line_to_stmt_map[node.lineno] = stmt_node
        for field, value in ast.iter_fields(node):
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        q.appendleft((item, stmt_node))
            elif isinstance(value, ast.AST):
                q.appendleft((value, stmt_node))
    return line_to_stmt_map
