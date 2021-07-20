# -*- coding: future_annotations -*-
import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Union


EMIT_EVENT = '_X5ix_NBSAFETY_EVT_EMIT'
TRACING_ENABLED = '_X5ix_NBSAFETY_TRACING_ENABLED'


def make_loop_iter_flag_name(loop_node: Union[int, ast.AST]):
    loop_node_id = loop_node if isinstance(loop_node, int) else id(loop_node)
    return '_X5ix_NBSAFETY_LOOPED_ONCE_{}'.format(loop_node_id)
