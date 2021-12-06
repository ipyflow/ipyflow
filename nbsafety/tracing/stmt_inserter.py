# -*- coding: future_annotations -*-
import ast
import logging
from typing import cast, TYPE_CHECKING

from nbsafety.extra_builtins import EMIT_EVENT, TRACING_ENABLED, make_loop_guard_name
from nbsafety.tracing.trace_events import TraceEvent
from nbsafety.utils.ast_utils import EmitterMixin, make_test, make_composite_condition
from nbsafety.utils import fast

if TYPE_CHECKING:
    from typing import Dict, FrozenSet, List, Set, Union


logger = logging.getLogger(__name__)


_INSERT_STMT_TEMPLATE = '{}("{{evt}}", {{stmt_id}})'.format(EMIT_EVENT)


def _get_parsed_insert_stmt(stmt: ast.stmt, evt: TraceEvent) -> ast.stmt:
    with fast.location_of(stmt):
        return fast.parse(_INSERT_STMT_TEMPLATE.format(evt=evt.value, stmt_id=id(stmt))).body[0]


def _get_parsed_append_stmt(
    stmt: ast.stmt, ret_expr: ast.expr = None, evt: TraceEvent = TraceEvent.after_stmt, **kwargs
) -> ast.stmt:
    with fast.location_of(stmt):
        ret = cast(ast.Expr, _get_parsed_insert_stmt(stmt, evt))
        if ret_expr is not None:
            kwargs['ret'] = ret_expr
        ret_value = cast(ast.Call, ret.value)
        ret_value.keywords = fast.kwargs(**kwargs)
    ret.lineno = getattr(stmt, 'end_lineno', ret.lineno)
    return ret


class StripGlobalAndNonlocalDeclarations(ast.NodeTransformer):
    def visit_Global(self, node: ast.Global) -> ast.Pass:
        with fast.location_of(node):
            return fast.Pass()

    def visit_Nonlocal(self, node: ast.Nonlocal) -> ast.Pass:
        with fast.location_of(node):
            return fast.Pass()


class StatementInserter(ast.NodeTransformer, EmitterMixin):
    def __init__(
        self,
        orig_to_copy_mapping: Dict[int, ast.AST],
        events_with_handlers: FrozenSet[TraceEvent],
        loop_guards: Set[str],
    ):
        EmitterMixin.__init__(self, orig_to_copy_mapping, events_with_handlers)
        self._loop_guards: Set[str] = loop_guards
        self._init_stmt_inserted: bool = False
        self._global_nonlocal_stripper: StripGlobalAndNonlocalDeclarations = StripGlobalAndNonlocalDeclarations()

    def _handle_loop_body(self, node: Union[ast.For, ast.While], orig_body: List[ast.AST]) -> List[ast.AST]:
        loop_node_copy = cast('Union[ast.For, ast.While]', self.orig_to_copy_mapping[id(node)])
        loop_node_copy = self._global_nonlocal_stripper.visit(loop_node_copy)
        loop_guard = make_loop_guard_name(loop_node_copy)
        self._loop_guards.add(loop_guard)
        with fast.location_of(loop_node_copy):
            if isinstance(node, ast.For):
                before_loop_evt = TraceEvent.before_for_loop_body
                after_loop_evt = TraceEvent.after_for_loop_iter
            else:
                before_loop_evt = TraceEvent.before_while_loop_body
                after_loop_evt = TraceEvent.after_while_loop_iter
            return [
                fast.If(
                    test=make_composite_condition(
                        [
                            make_test(TRACING_ENABLED),
                            make_test(loop_guard, negate=True),
                            self.emit(
                                before_loop_evt, node, ret=fast.NameConstant(True)
                            ) if before_loop_evt in self.events_with_handlers else None,
                        ]
                    ),
                    body=[
                        fast.Try(
                            body=orig_body,
                            handlers=[],
                            orelse=[],
                            finalbody=[
                                _get_parsed_append_stmt(
                                    cast(ast.stmt, loop_node_copy),
                                    evt=after_loop_evt,
                                    loop_guard=fast.Str(loop_guard),
                                ),
                            ],
                        ),
                    ] if after_loop_evt in self.events_with_handlers else orig_body,
                    orelse=loop_node_copy.body,
                ),
            ]

    def _handle_function_body(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef], orig_body: List[ast.AST]
    ) -> List[ast.AST]:
        fundef_copy = cast('Union[ast.FunctionDef, ast.AsyncFunctionDef]', self.orig_to_copy_mapping[id(node)])
        fundef_copy = self._global_nonlocal_stripper.visit(fundef_copy)
        with fast.location_of(fundef_copy):
            return [
                fast.If(
                    test=make_composite_condition(
                        [
                            make_test(TRACING_ENABLED),
                            self.emit(
                                TraceEvent.before_function_body, node, ret=fast.NameConstant(True)
                            ) if TraceEvent.before_function_body in self.events_with_handlers else None,
                        ]
                    ),
                    body=[
                        fast.Try(
                            body=orig_body,
                            handlers=[],
                            orelse=[],
                            finalbody=[
                                _get_parsed_append_stmt(
                                    cast(ast.stmt, fundef_copy),
                                    evt=TraceEvent.after_function_execution,
                                ),
                            ],
                        ),
                    ] if TraceEvent.after_function_execution in self.events_with_handlers else orig_body,
                    orelse=fundef_copy.body,
                ),
            ]

    def generic_visit(self, node):
        for name, field in ast.iter_fields(node):
            if isinstance(field, ast.AST):
                setattr(node, name, self.visit(field))
            elif isinstance(field, list):
                new_field = []
                for inner_node in field:
                    if isinstance(inner_node, ast.stmt):
                        stmt_copy = cast(ast.stmt, self.orig_to_copy_mapping[id(inner_node)])
                        if TraceEvent.init_module in self.events_with_handlers:
                            if not self._init_stmt_inserted:
                                assert isinstance(node, ast.Module)
                                self._init_stmt_inserted = True
                                with fast.location_of(stmt_copy):
                                    new_field.extend(fast.parse(
                                        f'import builtins; {EMIT_EVENT}("{TraceEvent.init_module.value}", None)'
                                    ).body)
                        if TraceEvent.before_stmt in self.events_with_handlers:
                            new_field.append(_get_parsed_insert_stmt(stmt_copy, TraceEvent.before_stmt))
                        if TraceEvent.after_stmt in self.events_with_handlers:
                            if isinstance(inner_node, ast.Expr) and isinstance(node, ast.Module) and name == 'body':
                                val = inner_node.value
                                while isinstance(val, ast.Expr):
                                    val = val.value
                                new_field.append(_get_parsed_append_stmt(stmt_copy, ret_expr=val))
                            else:
                                new_field.append(self.visit(inner_node))
                                if not isinstance(inner_node, ast.Return):
                                    new_field.append(_get_parsed_append_stmt(stmt_copy))
                        else:
                            new_field.append(self.visit(inner_node))
                        if TraceEvent.after_module_stmt in self.events_with_handlers:
                            if isinstance(node, ast.Module) and name == 'body':
                                assert not isinstance(inner_node, ast.Return)
                                new_field.append(_get_parsed_append_stmt(stmt_copy, evt=TraceEvent.after_module_stmt))
                    elif isinstance(inner_node, ast.AST):
                        new_field.append(self.visit(inner_node))
                    else:
                        new_field.append(inner_node)
                if name == 'body':
                    if isinstance(node, (ast.For, ast.While)):
                        new_field = self._handle_loop_body(node, new_field)
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        new_field = self._handle_function_body(node, new_field)
                setattr(node, name, new_field)
            else:
                continue
        return node
