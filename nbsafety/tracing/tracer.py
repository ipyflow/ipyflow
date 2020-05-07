# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import TYPE_CHECKING

# from IPython import get_ipython

from .trace_stmt import TraceStatement
from .trace_events import TraceEvent
from .trace_state import TraceState

if TYPE_CHECKING:
    from typing import Any
    from types import FrameType
    from ..safety import DependencySafety


def make_tracer(safety: DependencySafety):
    def tracer(frame: FrameType, evt: str, arg: Any):
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

        if state.last_event == TraceEvent.exception:
            # TODO: unwind the stack
            pass

        cell_num, lineno = TraceState.get_position(frame)
        stmt_node = safety.statement_cache[cell_num][lineno]
        # source = get_ipython().all_ns_refs[0]['In'][cell_num].strip().split('\n')
        # print(lineno, event, source[lineno-1])
        # print(lineno, event, stmt_node, source[lineno-1])

        trace_stmt = state.traced_statements.get(
            id(stmt_node),
            TraceStatement(safety, frame, stmt_node, state.cur_frame_scope)
        )
        state.traced_statements[id(stmt_node)] = trace_stmt
        state.state_transition_hook(frame, event, arg, trace_stmt)
        return tracer
    return tracer
