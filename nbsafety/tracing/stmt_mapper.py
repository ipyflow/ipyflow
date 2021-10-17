# -*- coding: future_annotations -*-
import ast
import copy
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Dict, List, Optional, Set, Tuple
    from nbsafety.types import CellId


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class StatementMapper(ast.NodeVisitor):
    def __init__(
        self,
        cell_id: Optional[CellId],
        line_to_stmt_map: Dict[int, ast.stmt],
        id_map: Dict[int, ast.AST],
        parent_map: Dict[int, ast.AST],
        reactive_variable_node_ids: Set[int],
        reactive_attribute_node_ids: Set[int],
        blocking_variable_node_ids: Set[int],
        blocking_attribute_node_ids: Set[int],
        reactive_var_positions: Set[Tuple[int, int]],
        blocking_var_positions: Set[Tuple[int, int]],
    ):
        self._cell_id: Optional[CellId] = cell_id
        self.line_to_stmt_map = line_to_stmt_map
        self.id_map = id_map
        self.parent_map = parent_map
        self.reactive_variable_node_ids = reactive_variable_node_ids
        self.reactive_attribute_node_ids = reactive_attribute_node_ids
        self.blocking_variable_node_ids = blocking_variable_node_ids
        self.blocking_attribute_node_ids = blocking_attribute_node_ids
        self.reactive_var_positions = reactive_var_positions
        self.blocking_var_positions = blocking_var_positions
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
            if isinstance(nc, ast.Name):
                if (nc.lineno, nc.col_offset) in self.reactive_var_positions:
                    self.reactive_variable_node_ids.add(id(nc))
                elif (nc.lineno, nc.col_offset) in self.blocking_var_positions:
                    self.blocking_variable_node_ids.add(id(nc))
            elif isinstance(nc, ast.Attribute):
                lineno, col_offset = nc.lineno, getattr(nc.value, 'end_col_offset', -2) + 1
                if (lineno, col_offset) in self.reactive_var_positions:
                    self.reactive_attribute_node_ids.add(id(nc))
                elif (lineno, col_offset) in self.blocking_var_positions:
                    self.blocking_attribute_node_ids.add(id(nc))
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
