import ast
import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Dict, Set


class StatementInserter(ast.NodeTransformer):
    def __init__(
            self,
            prepend_stmt_template: str,
            append_stmt_template: str,
            line_to_stmt_map: 'Dict[int, int]',
            id_map: 'Dict[int, ast.stmt]',
    ):
        self._prepend_stmt_template = prepend_stmt_template
        self._append_stmt_template = append_stmt_template
        self.line_to_stmt_map = line_to_stmt_map
        self.id_map = id_map
        self.skip_nodes: 'Set[int]' = set()

    def _get_parsed_prepend_stmt(self, stmt_id: int):
        return ast.parse(self._prepend_stmt_template.format(stmt_id=stmt_id)).body[0]

    def _get_parsed_append_stmt(self, stmt_id: int):
        return ast.parse(self._append_stmt_template.format(stmt_id=stmt_id)).body[0]

    def visit(self, node):
        for name, field in ast.iter_fields(node):
            if isinstance(field, ast.AST):
                setattr(node, name, self.visit(field))
            elif isinstance(field, list):
                new_field = []
                for inner_node in field:
                    if isinstance(inner_node, ast.stmt):
                        stmt_copy = copy.deepcopy(inner_node)
                        self.id_map[id(stmt_copy)] = stmt_copy
                        self.line_to_stmt_map[inner_node.lineno] = id(stmt_copy)
                        prepend_stmt = self._get_parsed_prepend_stmt(id(stmt_copy))
                        new_field.append(prepend_stmt)
                        ast.copy_location(prepend_stmt, inner_node)
                        self.skip_nodes.add(id(prepend_stmt))
                        new_field.append(self.visit(inner_node))
                        if not isinstance(inner_node, ast.Return):
                            append_stmt = self._get_parsed_append_stmt(id(stmt_copy))
                            self.skip_nodes.add(id(append_stmt))
                            ast.copy_location(append_stmt, inner_node)
                            if hasattr(inner_node, 'end_lineno'):
                                append_stmt.lineno = inner_node.end_lineno
                            new_field.append(append_stmt)
                    elif isinstance(inner_node, ast.AST):
                        new_field.append(self.visit(inner_node))
                    else:
                        new_field.append(inner_node)
                setattr(node, name, new_field)
            else:
                continue
        return node

        # if hasattr(node, 'handlers'):
        #     new_handlers = []
        #     for handler in node.handlers:
        #         new_handlers.append(self.visit(handler))
        #     node.handlers = new_handlers
        # if not hasattr(node, 'body'):
        #     return node
        # if not all(isinstance(nd, ast.stmt) for nd in node.body):
        #     return node
        # new_stmts = []
        # for stmt in node.body:
        #     prepend_stmt = self._get_parsed_prepend_stmt()
        #     append_stmt = self._get_parsed_append_stmt(stmt)
        #     ast.copy_location(prepend_stmt, stmt)
        #     ast.copy_location(append_stmt, stmt)
        #     self.skip_nodes.add(id(prepend_stmt))
        #     self.skip_nodes.add(id(append_stmt))
        #     new_stmts.append(prepend_stmt)
        #     new_stmts.append(self.visit(stmt))
        #     new_stmts.append(append_stmt)
        # node.body = new_stmts
        # return node
