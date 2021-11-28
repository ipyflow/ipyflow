# -*- coding: future_annotations -*-
import ast
import sys
from typing import TYPE_CHECKING

from nbsafety.extra_builtins import EMIT_EVENT
from nbsafety.tracing.trace_events import TraceEvent
from nbsafety.utils import fast

if TYPE_CHECKING:
    from typing import Dict, FrozenSet, Union


class EmitterMixin:
    def __init__(self, orig_to_copy_mapping: Dict[int, ast.AST], events_with_handlers: FrozenSet[TraceEvent]):
        self._orig_to_copy_mapping = orig_to_copy_mapping
        self._events_with_handlers = events_with_handlers

    def emitter_ast(self):
        return fast.Name(EMIT_EVENT, ast.Load())

    def get_copy_id_ast(self, orig_node_id: Union[int, ast.AST]):
        if not isinstance(orig_node_id, int):
            orig_node_id = id(orig_node_id)
        return fast.Num(id(self._orig_to_copy_mapping[orig_node_id]))

    def make_tuple_event_for(self, node: ast.AST, event: TraceEvent, orig_node_id=None, **kwargs):
        if event not in self._events_with_handlers:
            return node
        with fast.location_of(node):
            tuple_node = fast.Tuple(
                [
                    fast.Call(
                        func=self.emitter_ast(),
                        args=[event.to_ast(), self.get_copy_id_ast(orig_node_id or node)],
                        keywords=fast.kwargs(**kwargs),
                    ),
                    node,
                ],
                ast.Load()
            )
            slc: Union[ast.Constant, ast.Num, ast.Index] = fast.Num(1)
            if sys.version_info < (3, 9):
                slc = fast.Index(slc)
            return fast.Subscript(tuple_node, slc, ast.Load())


def subscript_to_slice(node: ast.Subscript) -> ast.expr:
    if isinstance(node.slice, ast.Index):
        return node.slice.value  # type: ignore
    else:
        return node.slice  # type: ignore
