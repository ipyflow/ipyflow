# -*- coding: utf-8 -*-
import ast
import logging
from typing import TYPE_CHECKING

from .trace_events import TraceEvent

if TYPE_CHECKING:
    from typing import Dict, List, Optional
    from types import FrameType
    from ..safety import DependencySafety
    from .trace_stmt import TraceStatement

logger = logging.getLogger(__name__)


class TraceState(object):
    def __init__(self, safety: 'DependencySafety'):
        self.safety = safety
        self.cur_frame_scope = safety.global_scope
        self.prev_trace_stmt_in_cur_frame: Optional[TraceStatement] = None
        self.call_depth = 0
        self.traced_statements: Dict[int, TraceStatement] = {}
        self.stack: List[TraceStatement] = []
        self.source: Optional[str] = None
        self.prev_trace_stmt: Optional[TraceStatement] = None
        self.prev_event: Optional[TraceEvent] = None
        self.error_occurred = False

    def _check_prev_stmt_done_executing_hook(self, event: 'TraceEvent', trace_stmt: 'TraceStatement'):
        if event not in (
                TraceEvent.line, TraceEvent.return_
        ) or self.prev_event in (
                TraceEvent.call, TraceEvent.exception
        ):
            return

        # we'll be needing these
        prev_this_frame = self.prev_trace_stmt_in_cur_frame
        prev_overall = self.prev_trace_stmt

        if prev_overall != trace_stmt:
            self.safety.attr_trace_manager.stmt_transition_hook()

        if event == TraceEvent.return_:
            if prev_overall is not None and prev_overall is not self.stack[-1]:
                prev_overall.finished_execution_hook()

        if self.prev_event == TraceEvent.return_:
            if prev_this_frame is not None:
                if len(self.stack) == 0 or prev_this_frame is not self.stack[-1]:
                    # this condition ensures we're not inside of a list comprehension or something with multiple calls
                    prev_this_frame.finished_execution_hook()
            return

        if prev_this_frame is None or prev_this_frame.marked_finished:
            return

        finished = prev_this_frame is not trace_stmt
        finished = finished and not (
            # classdefs are not finished until we reach the end of the class body
                isinstance(prev_this_frame.stmt_node, ast.ClassDef) and self.prev_event != TraceEvent.return_
        )
        if finished:
            prev_this_frame.finished_execution_hook()

    def state_transition_hook(
            self,
            event: 'TraceEvent',
            trace_stmt: 'TraceStatement'
    ):
        self.safety.trace_event_counter[0] += 1

        self._check_prev_stmt_done_executing_hook(event, trace_stmt)

        self.prev_trace_stmt = trace_stmt
        if event == TraceEvent.line:
            self.prev_trace_stmt_in_cur_frame = trace_stmt
        if event == TraceEvent.call:
            self.stack.append(self.prev_trace_stmt_in_cur_frame)
            # print('scope', trace_stmt.scope)
            with trace_stmt.replace_active_scope(self.safety.attr_trace_manager.active_scope_for_call):
                # print('active scope', trace_stmt.scope)
                self.cur_frame_scope = trace_stmt.get_post_call_scope(self.cur_frame_scope)
                # print('post call scope', self.cur_frame_scope)
            logger.debug('entering scope %s', self.cur_frame_scope)
            self.prev_trace_stmt_in_cur_frame = None
            self.safety.attr_trace_manager.push_stack(self.cur_frame_scope)
        if event == TraceEvent.return_:
            logger.debug('leaving scope %s', self.cur_frame_scope)
            return_to_stmt = self.stack.pop()
            assert return_to_stmt is not None
            if self.prev_event != TraceEvent.exception:
                # exception events are followed by return events until we hit an except clause
                # no need to track dependencies in this case
                if isinstance(return_to_stmt.stmt_node, ast.ClassDef):
                    return_to_stmt.class_scope = self.cur_frame_scope
                else:
                    return_to_stmt.call_point_deps.append(trace_stmt.compute_rval_dependencies())
            # reset for the previous frame, so that we push it again if it has another funcall
            self.prev_trace_stmt_in_cur_frame = return_to_stmt
            # self.cur_frame_scope = return_to_stmt.scope
            self.safety.attr_trace_manager.pop_stack()
            self.cur_frame_scope = self.safety.attr_trace_manager.active_scope
            logger.debug('entering scope %s', self.cur_frame_scope)
        self.prev_event = event

    @staticmethod
    def get_position(frame: 'FrameType'):
        cell_num = int(frame.f_code.co_filename.split('-')[2])
        return cell_num, frame.f_lineno
