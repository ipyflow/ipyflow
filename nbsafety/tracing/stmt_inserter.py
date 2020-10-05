import ast
import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Union


class StatementInserter(ast.NodeTransformer):
    def __init__(self, stmt_to_insert_before: 'Union[str, ast.stmt]'):
        if isinstance(stmt_to_insert_before, str):
            self.stmt_to_insert_before = ast.parse(stmt_to_insert_before).body[0]
        else:
            self.stmt_to_insert_before = stmt_to_insert_before

    def visit(self, node: 'ast.AST'):
        if not hasattr(node, 'body'):
            return node
        if not all(isinstance(nd, ast.stmt) for nd in node.body):
            return node
        new_stmts = []
        for stmt in node.body:
            insert_stmt = copy.deepcopy(self.stmt_to_insert_before)
            ast.copy_location(insert_stmt, stmt)
            new_stmts.append(insert_stmt)
            new_stmts.append(self.visit(stmt))
        node.body = new_stmts
        return node
