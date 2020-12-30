# -*- coding: utf-8 -*-
import ast
from typing import TYPE_CHECKING
from collections import deque

if TYPE_CHECKING:
    from typing import Deque, Dict, Optional, Tuple, Union


def compute_lineno_to_stmt_mapping(node: 'Union[str, ast.AST]') -> 'Dict[int, ast.stmt]':
    line_to_stmt_map = {}
    if isinstance(node, str):
        node = ast.parse(node)
    q: Deque[Tuple[ast.AST, Optional[ast.stmt]]] = deque([(node, None)])
    while len(q) > 0:
        node, stmt_parent = q.pop()
        if isinstance(node, ast.stmt):
            stmt_parent = node
        if stmt_parent is not None and hasattr(node, 'lineno'):
            line_to_stmt_map[node.lineno] = stmt_parent
        for field, value in ast.iter_fields(node):
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        q.appendleft((item, stmt_parent))
            elif isinstance(value, ast.AST):
                q.appendleft((value, stmt_parent))
    return line_to_stmt_map
