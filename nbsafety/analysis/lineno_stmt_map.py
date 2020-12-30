# -*- coding: utf-8 -*-
import ast
import copy
from typing import cast, TYPE_CHECKING
from collections import deque

if TYPE_CHECKING:
    from typing import Deque, Dict, Optional, Tuple, Union


def compute_lineno_to_stmt_mapping(
        node: 'Union[str, ast.AST]',
        line_to_stmt_map: 'Optional[Dict[int, Union[int, ast.stmt]]]' = None,
        id_map: 'Optional[Dict[int, ast.stmt]]' = None,
        make_copy: bool = False
):
    if line_to_stmt_map is None:
        line_to_stmt_map = {}
    if isinstance(node, str):
        node = ast.parse(node)
    q: Deque[Tuple[ast.AST, Optional[ast.stmt]]] = deque([(node, None)])
    while len(q) > 0:
        node, stmt_node = q.pop()
        if isinstance(node, ast.stmt):
            stmt_node = node
        if stmt_node is not None and hasattr(node, 'lineno'):
            if make_copy:
                node_to_use = copy.deepcopy(stmt_node)
            else:
                node_to_use = stmt_node
            if id_map is None:
                line_to_stmt_map[node.lineno] = stmt_node
            else:
                line_to_stmt_map[node.lineno] = id(stmt_node)
                id_map[id(stmt_node)] = node_to_use
        for field, value in ast.iter_fields(node):
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        q.appendleft((item, stmt_node))
            elif isinstance(value, ast.AST):
                q.appendleft((value, stmt_node))
    return line_to_stmt_map


class ComputeLinenoToStmtMapping(ast.NodeTransformer):
    def __init__(self, line_to_stmt_map: 'Dict[int, int]', id_map: 'Dict[int, ast.stmt]'):
        self.line_to_stmt_map = line_to_stmt_map
        self.id_map = id_map

    def visit(self, node: 'ast.AST'):
        compute_lineno_to_stmt_mapping(
            node,
            line_to_stmt_map=cast('Dict[int, Union[int, ast.stmt]]', self.line_to_stmt_map),
            id_map=self.id_map,
            make_copy=True
        )
        return node
