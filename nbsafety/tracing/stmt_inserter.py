# -*- coding: future_annotations -*-
import ast
from typing import cast, TYPE_CHECKING

from nbsafety.tracing.trace_events import TraceEvent, EMIT_EVENT
from nbsafety.utils import fast

if TYPE_CHECKING:
    from typing import Dict, Optional, Set
    from nbsafety.types import CellId


class StatementInserter(ast.NodeTransformer):
    def __init__(self, cell_id: Optional[CellId], orig_to_copy_mapping: Dict[int, ast.AST]):
        self._cell_id: Optional[CellId] = cell_id
        self._orig_to_copy_mapping = orig_to_copy_mapping
        self._prepend_stmt_template = '{}("{}", {{stmt_id}})'.format(EMIT_EVENT, TraceEvent.before_stmt.value)
        self._append_stmt_template = '{}("{}", {{stmt_id}})'.format(EMIT_EVENT, TraceEvent.after_stmt.value)
        self._init_stmt_inserted = False

    def _get_parsed_prepend_stmt(self, stmt: ast.stmt) -> ast.stmt:
        with fast.location_of(stmt):
            return fast.parse(self._prepend_stmt_template.format(stmt_id=id(stmt))).body[0]

    def _get_parsed_append_stmt(self, stmt: ast.stmt, ret_expr: ast.expr = None) -> ast.stmt:
        with fast.location_of(stmt):
            ret = cast(ast.Expr, fast.parse(self._append_stmt_template.format(stmt_id=id(stmt))).body[0])
            if ret_expr is not None:
                ret_value = cast(ast.Call, ret.value)
                ret_value.keywords = fast.kwargs(ret=ret_expr)
        ret.lineno = getattr(stmt, 'end_lineno', ret.lineno)
        return ret

    def visit(self, node):
        for name, field in ast.iter_fields(node):
            if isinstance(field, ast.AST):
                setattr(node, name, self.visit(field))
            elif isinstance(field, list):
                new_field = []
                for inner_node in field:
                    if isinstance(inner_node, ast.stmt):
                        stmt_copy = cast(ast.stmt, self._orig_to_copy_mapping[id(inner_node)])
                        if not self._init_stmt_inserted:
                            self._init_stmt_inserted = True
                            with fast.location_of(stmt_copy):
                                new_field.append(fast.parse(
                                    f'{EMIT_EVENT}("{TraceEvent.init_cell.value}", None, cell_id="{self._cell_id}")'
                                ).body[0])
                        new_field.append(self._get_parsed_prepend_stmt(stmt_copy))
                        if isinstance(inner_node, ast.Expr):
                            val = inner_node.value
                            while isinstance(val, ast.Expr):
                                val = val.value
                            new_field.append(self._get_parsed_append_stmt(stmt_copy, ret_expr=val))
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
