# -*- coding: utf-8 -*-
from __future__ import annotations
import ast
import logging
from typing import TYPE_CHECKING

from .trace_events import TraceEvent

if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional
    from types import FrameType
    from ..safety import DependencySafety
    from .trace_stmt import TraceStatement


class TraceState(object):
    def __init__(self, safety: DependencySafety):
        self.safety = safety
        self.cur_frame_scope = safety.global_scope
        self.prev_trace_stmt_in_cur_frame: Optional[TraceStatement] = None
        self.call_depth = 0
        self.code_statements: Dict[int, TraceStatement] = {}
        self.stack: List[TraceStatement] = []
        self.source: Optional[str] = None
        self.prev_trace_stmt: Optional[TraceStatement] = None
        self.last_event: Optional[TraceEvent] = None

    def _prev_stmt_done_executing(self, event: TraceEvent, code_stmt: TraceStatement):
        if event not in (
                TraceEvent.line, TraceEvent.return_
        ) or self.last_event in (
                TraceEvent.call, TraceEvent.exception
        ):
            return False
        finished = self.prev_trace_stmt is not code_stmt
        if self.prev_trace_stmt is not None:
            finished = finished and not (
                # classdefs are not finished until we reach the end of the class body
                    isinstance(self.prev_trace_stmt.stmt_node, ast.ClassDef) and event != TraceEvent.return_
            )
        return finished

    def state_transition_hook(
            self,
            frame: FrameType,
            event: TraceEvent,
            arg: Any,
            trace_stmt: TraceStatement
    ):
        if self._prev_stmt_done_executing(event, trace_stmt) and self.prev_trace_stmt_in_cur_frame is not None:
            # TODO (smacke): maybe put this branch in TraceStatement.update_hook() or something
            # need to handle namespace cloning upon object creation still
            self.prev_trace_stmt_in_cur_frame.finished_execution_hook()

        self.prev_trace_stmt = trace_stmt
        if event == TraceEvent.line:
            self.prev_trace_stmt_in_cur_frame = trace_stmt
        if event == TraceEvent.call:
            self.stack.append(self.prev_trace_stmt_in_cur_frame)
            self.cur_frame_scope = trace_stmt.get_post_call_scope(self.cur_frame_scope)
            logging.debug('entering scope %s', self.cur_frame_scope)
            self.prev_trace_stmt_in_cur_frame = None
        if event == TraceEvent.return_:
            logging.debug('leaving scope %s', self.cur_frame_scope)
            return_to_stmt = self.stack.pop()
            assert return_to_stmt is not None
            if not isinstance(return_to_stmt.stmt_node, ast.ClassDef):
                return_to_stmt.call_point_dependencies.append(trace_stmt.compute_rval_dependencies())
                return_to_stmt.call_point_retvals.append(arg)
            # reset 'cur_frame_last_line' for the previous frame, so that we push it again if it has another funcall
            self.prev_trace_stmt_in_cur_frame = return_to_stmt
            self.cur_frame_scope = return_to_stmt.scope
            logging.debug('entering scope %s', self.cur_frame_scope)
        if event == TraceEvent.exception:
            # TODO: save off the frame. when we hit the next trace event (the except clause), we'll count the
            # number of times we need to pop the saved frame in order to determine how many times to pop
            # our trace state's bespoke stack. See the `self.last_event == 'exception` comment in `tracer`.
            pass
        self.last_event = event

    @staticmethod
    def get_position(frame: FrameType):
        cell_num = int(frame.f_code.co_filename.split('-')[2])
        return cell_num, frame.f_lineno
