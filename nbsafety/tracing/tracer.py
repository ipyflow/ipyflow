# -*- coding: utf-8 -*-
import logging
from typing import TYPE_CHECKING

from IPython import get_ipython

from .trace_stmt import TraceStatement
from .trace_events import TraceEvent
from .trace_state import TraceState

if TYPE_CHECKING:
    from types import FrameType
    from ..safety import DependencySafety

logger = logging.getLogger(__name__)


def make_tracer(safety: 'DependencySafety'):
    if safety.trace_messages_enabled:
        logger.setLevel(logging.WARNING)
    else:
        logger.setLevel(logging.ERROR)

    def tracer(frame: 'FrameType', evt: str, _):
        # notebook cells have filenames that appear as '<ipython-input...>'
        if not frame.f_code.co_filename.startswith('<ipython-input'):
            return

        event = TraceEvent(evt)
        state = safety.trace_state  # we'll be using this a lot

        # IPython quirk -- every line in outer scope apparently wrapped in lambda
        # We want to skip the outer 'call' and 'return' for these
        if event == TraceEvent.call:
            state.call_depth += 1
            if state.call_depth == 1:
                return tracer

        if event == TraceEvent.return_:
            state.call_depth -= 1
            if state.call_depth == 0:
                return tracer

        cell_num, lineno = TraceState.get_position(frame)
        try:
            stmt_node = safety.statement_cache[cell_num][lineno]
        except KeyError:
            return tracer
        if safety.store_history and logger.getEffectiveLevel() <= logging.WARNING:
            try:
                source = get_ipython().all_ns_refs[0]['In'][cell_num].strip().split('\n')
                logger.warning(' %3d: %9s >>> %s', lineno, event, source[lineno-1])
            except (KeyError, IndexError) as e:
                logger.error('%s: cell %d, line %d', e, cell_num, lineno)

        trace_stmt = state.traced_statements.get(
            id(stmt_node),
            TraceStatement(safety, frame, stmt_node, state.cur_frame_scope)
        )
        state.traced_statements[id(stmt_node)] = trace_stmt
        state.state_transition_hook(event, trace_stmt)
        return tracer
    return tracer
