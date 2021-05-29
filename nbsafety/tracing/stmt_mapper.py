# -*- coding: future_annotations -*-
import ast
import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Dict, List, Optional, Set
    from nbsafety.types import CellId


class StatementMapper(ast.NodeVisitor):
    def __init__(
        self,
        cell_id: Optional[CellId],
        line_to_stmt_map: Dict[int, ast.stmt],
        id_map: Dict[int, ast.AST],
        cell_id_by_ast_id: Dict[int, CellId],
        parent_map: Dict[int, ast.AST],
    ):
        self._cell_id: Optional[CellId] = cell_id
        self.line_to_stmt_map = line_to_stmt_map
        self.id_map = id_map
        self.cell_id_by_ast_id = cell_id_by_ast_id
        self.parent_map = parent_map
        self.traversal: List[ast.AST] = []

    def __call__(self, node: ast.Module) -> Dict[int, ast.AST]:
        # for some bizarre reason we need to visit once to clear empty nodes apparently
        self.visit(node)
        self.traversal.clear()
        
        self.visit(node)
        orig_traversal = self.traversal
        self.traversal = []
        self.visit(copy.deepcopy(node))
        copy_traversal = self.traversal
        orig_to_copy_mapping = {}
        for no, nc in zip(orig_traversal, copy_traversal):
            orig_to_copy_mapping[id(no)] = nc
            self.id_map[id(nc)] = nc
            self.cell_id_by_ast_id[id(nc)] = self._cell_id
            if isinstance(nc, ast.stmt):
                self.line_to_stmt_map[nc.lineno] = nc
                # workaround for python >= 3.8 wherein function calls seem
                # to yield trace frames that use the lineno of the first decorator
                for decorator in getattr(nc, 'decorator_list', []):
                    self.line_to_stmt_map[decorator.lineno] = nc
                nc_body = getattr(nc, 'body', [])
                try:
                    for child in nc_body:
                        if isinstance(child, ast.AST):
                            self.parent_map[id(child)] = nc
                except TypeError:
                    self.parent_map[id(nc_body)] = nc
                for name, field in ast.iter_fields(nc):
                    if name == 'body':
                        continue
                    if isinstance(field, list):
                        for child in field:
                            if isinstance(child, ast.AST):
                                self.parent_map[id(child)] = nc
        return orig_to_copy_mapping

    def visit(self, node):
        self.traversal.append(node)
        for name, field in ast.iter_fields(node):
            if isinstance(field, ast.AST):
                self.visit(field)
            elif isinstance(field, list):
                for inner_node in field:
                    if isinstance(inner_node, ast.AST):
                        self.visit(inner_node)
