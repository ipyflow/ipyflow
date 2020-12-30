import ast
import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Dict, Optional, Set


class StatementInserter(ast.NodeTransformer):
    def __init__(
            self,
            line_to_stmt_map: 'Dict[int, ast.stmt]',
            id_map: 'Dict[int, ast.stmt]',
            prepend_stmt_template: 'Optional[str]' = None,
            append_stmt_template: 'Optional[str]' = None,
    ):
        self.line_to_stmt_map = line_to_stmt_map
        self.id_map = id_map
        self.skip_nodes: 'Set[int]' = set()
        self._prepend_stmt_template = prepend_stmt_template
        self._append_stmt_template = append_stmt_template

    def _get_parsed_prepend_stmt(self, stmt: 'ast.stmt') -> 'Optional[ast.stmt]':
        if self._prepend_stmt_template is None:
            return None
        ret = ast.parse(self._prepend_stmt_template.format(stmt_id=id(stmt))).body[0]
        ast.copy_location(ret, stmt)
        self.skip_nodes.add(id(ret))
        return ret

    def _get_parsed_append_stmt(self, stmt: 'ast.stmt') -> 'Optional[ast.stmt]':
        if self._append_stmt_template is None:
            return None
        ret = ast.parse(self._append_stmt_template.format(stmt_id=id(stmt))).body[0]
        ast.copy_location(ret, stmt)
        if hasattr(stmt, 'end_lineno'):
            ret.lineno = stmt.end_lineno
        self.skip_nodes.add(id(ret))
        return ret

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
                        self.line_to_stmt_map[inner_node.lineno] = stmt_copy
                        prepend_stmt = self._get_parsed_prepend_stmt(stmt_copy)
                        if prepend_stmt is not None:
                            new_field.append(prepend_stmt)
                        new_field.append(self.visit(inner_node))
                        if not isinstance(inner_node, ast.Return):
                            append_stmt = self._get_parsed_append_stmt(stmt_copy)
                            if append_stmt is not None:
                                new_field.append(append_stmt)
                    elif isinstance(inner_node, ast.AST):
                        new_field.append(self.visit(inner_node))
                    else:
                        new_field.append(inner_node)
                setattr(node, name, new_field)
            else:
                continue
        return node


def compute_lineno_to_stmt_mapping(code: str) -> 'Dict[int, ast.stmt]':
    inserter = StatementInserter({}, {})
    inserter.visit(ast.parse(code))
    return inserter.line_to_stmt_map
