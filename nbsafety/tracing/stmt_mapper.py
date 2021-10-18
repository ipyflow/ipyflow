# -*- coding: future_annotations -*-
import ast
import copy
import logging
from typing import TYPE_CHECKING

from nbsafety.singletons import nbs

if TYPE_CHECKING:
    from typing import Dict, List, Optional, Set, Tuple, Union
    from nbsafety.types import CellId


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class StatementMapper(ast.NodeVisitor):
    def __init__(
        self,
        cell_id: Optional[CellId],
        reactive_var_positions: Set[Tuple[int, int]],
        blocking_var_positions: Set[Tuple[int, int]],
    ):
        self._cell_id: Optional[CellId] = cell_id
        self.line_to_stmt_map = nbs().statement_cache[nbs().cell_counter()]
        self.id_map = nbs().ast_node_by_id
        self.parent_map = nbs().parent_node_by_id
        self.reactive_node_ids = nbs().reactive_node_ids
        self.blocking_node_ids = nbs().blocking_node_ids
        self.reactive_var_positions = reactive_var_positions
        self.blocking_var_positions = blocking_var_positions
        self.traversal: List[ast.AST] = []

    @staticmethod
    def _get_col_offset_for(node: Union[ast.Name, ast.Attribute, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef]):
        if isinstance(node, ast.Name):
            return node.col_offset
        elif isinstance(node, ast.Attribute):
            return getattr(node.value, 'end_col_offset', -2) + 1
        elif isinstance(node, ast.FunctionDef):
            # TODO: can be different if more spaces between 'def' and function name
            return node.col_offset + 4
        elif isinstance(node, ast.ClassDef):
            # TODO: can be different if more spaces between 'class' and class name
            return node.col_offset + 6
        elif isinstance(node, ast.AsyncFunctionDef):
            # TODO: can be different if more spaces between 'async', 'def', and function name
            return node.col_offset + 10
        else:
            raise TypeError('unsupported node type for node %s' % node)

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
            if isinstance(nc, (ast.Name, ast.Attribute, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                col_offset = self._get_col_offset_for(nc)
                if (nc.lineno, col_offset) in self.reactive_var_positions:
                    self.reactive_node_ids.add(id(nc))
                elif (nc.lineno, col_offset) in self.blocking_var_positions:
                    self.blocking_node_ids.add(id(nc))
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
