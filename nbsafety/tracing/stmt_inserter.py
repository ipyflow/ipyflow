import ast
import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Dict, Optional, Set


class StatementInserter(ast.NodeTransformer):
    def __init__(
            self, insert_stmt_template: str, cell_counter: int, original_stmts: 'Optional[Dict[int, ast.AST]]' = None
    ):
        self._insert_stmt_template = insert_stmt_template
        self._cell_counter = cell_counter
        self._cur_line_id = 0
        self.skip_nodes: 'Set[int]' = set()
        self.original_stmts = original_stmts

    def _get_parsed_insert_stmt(self, stmt: 'ast.stmt'):
        stmt_id = id(stmt)
        if self.original_stmts is not None:
            copied = copy.deepcopy(stmt)
            self.original_stmts[id(copied)] = copied
        ret = ast.parse(self._insert_stmt_template.format(
            site_id=(self._cell_counter, self._cur_line_id),
            stmt_id=stmt_id,
        )).body[0]
        self._cur_line_id += 1
        return ret

    def visit(self, node):
        if hasattr(node, 'handlers'):
            new_handlers = []
            for handler in node.handlers:
                new_handlers.append(self.visit(handler))
            node.handlers = new_handlers
        if not hasattr(node, 'body'):
            return node
        if not all(isinstance(nd, ast.stmt) for nd in node.body):
            return node
        new_stmts = []
        for stmt in node.body:
            insert_stmt = self._get_parsed_insert_stmt(stmt)
            ast.copy_location(insert_stmt, stmt)
            self.skip_nodes.add(id(insert_stmt))
            new_stmts.append(insert_stmt)
            new_stmts.append(self.visit(stmt))
        node.body = new_stmts
        return node
