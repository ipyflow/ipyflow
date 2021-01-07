import ast
from typing import cast, TYPE_CHECKING

from nbsafety.tracing.hooks import TracingHook
from nbsafety.utils import fast

if TYPE_CHECKING:
    from typing import Dict, Set


class StatementInserter(ast.NodeTransformer):
    def __init__(self, eavesdropper: 'ast.NodeTransformer', orig_to_copy_mapping: 'Dict[int, ast.AST]'):
        self._eavesdropper = eavesdropper
        self._orig_to_copy_mapping = orig_to_copy_mapping
        self._prepend_stmt_template = '{}({{stmt_id}})'.format(TracingHook.before_stmt_tracer.value)
        self._append_stmt_template = '{}({{stmt_id}})'.format(TracingHook.after_stmt_tracer.value)
        self.skip_nodes: 'Set[int]' = set()

    def __call__(self, node: 'ast.AST'):
        ret_node = self.visit(node)
        return ret_node, self.skip_nodes

    def _get_parsed_prepend_stmt(self, stmt: 'ast.stmt') -> 'ast.stmt':
        with fast.location_of(stmt):
            ret = fast.parse(self._prepend_stmt_template.format(stmt_id=id(stmt))).body[0]
        self.skip_nodes.add(id(ret))
        return ret

    def _get_parsed_append_stmt(self, stmt: 'ast.stmt', ret_expr: 'ast.Expr' = None) -> 'ast.stmt':
        with fast.location_of(stmt):
            ret = cast('ast.Expr', fast.parse(self._append_stmt_template.format(stmt_id=id(stmt))).body[0])
            if ret_expr is not None:
                ret_value = cast('ast.Call', ret.value)
                ret_value.keywords = [fast.keyword(arg='ret_expr', value=ret_expr)]
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
                        new_field.append(self._get_parsed_prepend_stmt(stmt_copy))
                        if isinstance(inner_node, ast.Expr):
                            val = inner_node.value
                            while isinstance(val, ast.Expr):
                                val = val.value
                            new_field.append(
                                self._get_parsed_append_stmt(stmt_copy, ret_expr=self._eavesdropper.visit(val))
                            )
                        else:
                            new_field.append(self.visit(inner_node))
                            if not isinstance(inner_node, ast.Return):
                                new_field.append(self._get_parsed_append_stmt(stmt_copy))
                    elif isinstance(inner_node, ast.AST):
                        new_field.append(self.visit(inner_node))
                    else:
                        new_field.append(inner_node)
                setattr(node, name, new_field)
            else:
                continue
        return node
