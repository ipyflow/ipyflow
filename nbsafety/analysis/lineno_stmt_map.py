# -*- coding: utf-8 -*-
from __future__ import annotations
import ast
from collections import deque


def compute_lineno_to_stmt_mapping(node):
    q = deque([(node, None)])
    line_to_stmt_map = {}
    while len(q) > 0:
        node, stmt_node = q.pop()
        if isinstance(node, ast.stmt):
            stmt_node = node
        if stmt_node is not None:
            line_to_stmt_map[node.lineno] = stmt_node
        for field, value in ast.iter_fields(node):
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        q.appendleft((item, stmt_node))
            elif isinstance(value, ast.AST):
                q.appendleft((value, stmt_node))
    return line_to_stmt_map
