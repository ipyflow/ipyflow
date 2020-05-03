# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import TYPE_CHECKING

from .trace_stmt import TraceStatement
from .trace_events import TraceEvent
from .trace_state import TraceState

if TYPE_CHECKING:
    from types import FrameType
    from ..safety import DependencySafety


def make_tracer(safety: DependencySafety):
    def tracer(frame: FrameType, evt: str, _):
        event = TraceEvent(evt)

        # this is a bit of a hack to get the class out of the locals
        # - it relies on 'self' being used... normally a safe assumption!
        try:
            class_name = frame.f_locals['self'].__class__.__name__
        except (KeyError, AttributeError):
            class_name = "No Class"

        # notebook filenames appear as 'ipython-input...'
        if 'ipython-input' not in frame.f_code.co_filename:
            return

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
        # print(lineno, event, stmt_node)

        code_stmt = state.code_statements.get(
            id(stmt_node),
            TraceStatement(safety, stmt_node, state.cur_frame_scope)
        )
        state.code_statements[id(stmt_node)] = code_stmt
        state.update_hook(event, frame, code_stmt)
        return tracer
    return tracer
