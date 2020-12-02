# -*- coding: utf-8 -*-
import logging
from typing import TYPE_CHECKING
import sys

from IPython import get_ipython

from nbsafety.tracing.trace_stmt import TraceStatement
from nbsafety.tracing.trace_events import TraceEvent
from nbsafety.tracing.trace_state import TraceState

if TYPE_CHECKING:
    from types import FrameType
    from nbsafety.safety import NotebookSafety

logger = logging.getLogger(__name__)


def make_tracer(safety: 'NotebookSafety'):
    if safety.config.trace_messages_enabled:
        logger.setLevel(logging.WARNING)
    else:
        logger.setLevel(logging.ERROR)

    def tracer(frame: 'FrameType', evt: str, _):
        state = safety.trace_state  # we'll be using this a lot

        if state.tracing_reset_pending:
            assert TraceEvent(evt) == TraceEvent.call
            state.tracing_reset_pending = False
            call_depth = 0
            while frame is not None:
                if frame.f_code.co_filename.startswith('<ipython-input'):
                    call_depth += 1
                frame = frame.f_back
            if call_depth == 1 and state.call_depth == 0:
                call_depth = 0
            if call_depth != state.call_depth:
                state.safety.disable_tracing()
            return None
            # TODO: eventually we'd like to reenable tracing even when the call depth isn't mismatched
            # scopes_to_push = []
            # while frame is not None:
            #     if frame.f_code.co_filename.startswith('<ipython-input'):
            #         call_depth += 1
            #         fun_name = frame.f_code.co_name
            #         if fun_name == '<module>':
            #             if state.call_depth == 0:
            #                 state.call_depth = 1
            #             break
            #         cell_num, lineno = TraceState.get_position(frame)
            #         stmt_node = safety.statement_cache[cell_num][lineno]
            #         func_cell = state.safety.statement_to_func_cell[id(stmt_node)]
            #         scopes_to_push.append(func_cell.call_scope)
            #     frame = frame.f_back
            # scopes_to_push.reverse()
            # scopes_to_push = scopes_to_push[state.call_depth-1:]
            # for scope in scopes_to_push:
            #     state.safety.attr_trace_manager.push_stack(scope)
            # state.call_depth = call_depth
            # return None

        # notebook cells have filenames that appear as '<ipython-input...>'
        if not frame.f_code.co_filename.startswith('<ipython-input'):
            return

        event = TraceEvent(evt)

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

        # bytecode for a line w/ function call
        # no need to trace these, and we definitely want to skip the calls to reenable tracing
        if frame.f_code.co_code == b'e\x00d\x00\x83\x01\x01\x00d\x01S\x00':
            return tracer

        cell_num, lineno = TraceState.get_position(frame)

        try:
            stmt_node = safety.statement_cache[cell_num][lineno]
        except KeyError:
            return tracer
        if safety.config.store_history and logger.getEffectiveLevel() <= logging.WARNING:
            try:
                source = get_ipython().all_ns_refs[0]['In'][cell_num].strip().split('\n')
                logger.warning(' %3d: %9s >>> %s', lineno, event, source[lineno-1])
            except (KeyError, IndexError) as e:
                logger.error('%s: cell %d, line %d', e, cell_num, lineno)

        trace_stmt = state.traced_statements.get(id(stmt_node), None)
        if trace_stmt is None:
            trace_stmt = TraceStatement(safety, frame, stmt_node, state.cur_frame_scope)
            state.traced_statements[id(stmt_node)] = trace_stmt
        if event == TraceEvent.call:
            if trace_stmt.call_seen:
                state.call_depth -= 1
                if state.call_depth == 1:
                    state.call_depth = 0
                state.safety.disable_tracing()
                return None
            trace_stmt.call_seen = True
        state.state_transition_hook(event, trace_stmt)
        return tracer
    return tracer
