# -*- coding: future_annotations -*-
import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Union


EMIT_EVENT = '_X5ix_NBSAFETY_EVT_EMIT'
TRACING_ENABLED = '_X5ix_NBSAFETY_TRACING_ENABLED'


def make_guard_name(node: Union[int, ast.AST]):
    node_id = node if isinstance(node, int) else id(node)
    return '_X5ix_NBSAFETY_GUARD_{}'.format(node_id)
