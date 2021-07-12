# -*- coding: future_annotations -*-
import ast
import logging
from typing import cast, TYPE_CHECKING

from nbsafety.singletons import nbs  # FIXME: get rid of this
from nbsafety.tracing.trace_events import TraceEvent, EMIT_EVENT
from nbsafety.utils import fast

if TYPE_CHECKING:
    from typing import Dict, Optional, Set
    from nbsafety.types import CellId


logger = logging.getLogger(__name__)


_INSERT_STMT_TEMPLATE = '{}("{{evt}}", {{stmt_id}})'.format(EMIT_EVENT)


def _get_parsed_insert_stmt(stmt: ast.stmt, evt: TraceEvent) -> ast.stmt:
    with fast.location_of(stmt):
        return fast.parse(_INSERT_STMT_TEMPLATE.format(evt=evt.value, stmt_id=id(stmt))).body[0]


def _get_parsed_append_stmt(
    stmt: ast.stmt, ret_expr: ast.expr = None, evt: TraceEvent = TraceEvent.after_stmt
) -> ast.stmt:
    with fast.location_of(stmt):
        ret = cast(ast.Expr, _get_parsed_insert_stmt(stmt, evt))
        if ret_expr is not None:
            ret_value = cast(ast.Call, ret.value)
            ret_value.keywords = fast.kwargs(ret=ret_expr)
    ret.lineno = getattr(stmt, 'end_lineno', ret.lineno)
    return ret


class StatementInserter(ast.NodeTransformer):
    def __init__(self, cell_id: Optional[CellId], orig_to_copy_mapping: Dict[int, ast.AST]):
        self._cell_id: Optional[CellId] = cell_id
        self._orig_to_copy_mapping = orig_to_copy_mapping
        self._init_stmt_inserted = False

    def generic_visit(self, node):
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
                        new_field.append(_get_parsed_insert_stmt(stmt_copy, TraceEvent.before_stmt))
                        if isinstance(inner_node, ast.Expr) and isinstance(node, ast.Module) and name == 'body':
                            val = inner_node.value
                            while isinstance(val, ast.Expr):
                                val = val.value
                            new_field.append(_get_parsed_append_stmt(stmt_copy, ret_expr=val))
                        else:
                            new_field.append(self.visit(inner_node))
                            if not isinstance(inner_node, ast.Return):
                                new_field.append(_get_parsed_append_stmt(stmt_copy))
                        if isinstance(node, ast.Module) and name == 'body':
                            assert not isinstance(inner_node, ast.Return)
                            new_field.append(_get_parsed_append_stmt(stmt_copy, evt=TraceEvent.after_module_stmt))
                    elif isinstance(inner_node, ast.AST):
                        new_field.append(self.visit(inner_node))
                    else:
                        new_field.append(inner_node)
                if isinstance(node, (ast.For, ast.While)) and name == 'body':
                    loop_node_copy = self._orig_to_copy_mapping[id(node)]
                    new_field.append(
                        _get_parsed_append_stmt(
                            cast(ast.stmt, loop_node_copy),
                            evt=TraceEvent.after_loop_iter,
                        )
                    )
                    looped_once_flag = nbs().make_loop_iter_flag_name(loop_node_copy)
                    nbs().loop_iter_flag_names.add(looped_once_flag)
                    with fast.location_of(loop_node_copy):
                        new_field = [
                            fast.If(
                                test=fast.Name(looped_once_flag, ast.Load()),
                                body=loop_node_copy.body,
                                orelse=new_field,
                            ),
                        ]
                setattr(node, name, new_field)
            else:
                continue
        return node
