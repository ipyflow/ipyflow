import ast
from typing import cast, TYPE_CHECKING

from nbsafety.tracing.hooks import TracingHook

if TYPE_CHECKING:
    from typing import Dict, Optional, Set


class StatementInserter(ast.NodeTransformer):
    def __init__(self):
        self._prepend_stmt_template = '{}({{stmt_id}})'.format(TracingHook.before_stmt_tracer.value)
        self._append_stmt_template = '{}({{stmt_id}})'.format(TracingHook.after_stmt_tracer.value)
        self._orig_to_copy_mapping: 'Dict[int, ast.AST]' = {}
        self.skip_nodes: 'Set[int]' = set()

    def __call__(self, node: 'ast.AST', orig_to_copy_mapping: 'Dict[int, ast.AST]'):
        self._orig_to_copy_mapping = orig_to_copy_mapping
        ret_node = self.visit(node)
        return ret_node, (self.skip_nodes,)

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
        ret.lineno = getattr(stmt, 'end_lineno', ret.lineno)
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
                        stmt_copy = cast('ast.stmt', self._orig_to_copy_mapping[id(inner_node)])
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
