# -*- coding: utf-8 -*-
from __future__ import annotations
import ast
import logging
from typing import TYPE_CHECKING

from .trace_events import TraceEvent

if TYPE_CHECKING:
    from typing import Optional, Dict, List
    from types import FrameType
    from ..safety import DependencySafety
    from .trace_stmt import TraceStatement


class TraceState(object):
    def __init__(self, safety: DependencySafety):
        self.safety = safety
        self.cur_frame_scope = safety.global_scope
        self.cur_frame_last_stmt: Optional[TraceStatement] = None
        self.call_depth = 0
        self.code_statements: Dict[int, TraceStatement] = {}
        self.stack: List[TraceStatement] = []
        self.source: Optional[str] = None
        self.last_code_stmt: Optional[TraceStatement] = None
        self.last_event: Optional[TraceEvent] = None

    def _prev_stmt_done_executing(self, event: TraceEvent, code_stmt: TraceStatement):
        if event not in (
                TraceEvent.line, TraceEvent.return_
        ) or self.last_event in (
                TraceEvent.call, TraceEvent.exception
        ):
            return False
        finished = self.last_code_stmt is not code_stmt
        if self.last_code_stmt is not None:
            finished = finished and not (
                # classdefs are not finished until we reach the end of the class body
                isinstance(self.last_code_stmt.stmt_node, ast.ClassDef) and event != TraceEvent.return_
            )
        return finished

    def update_hook(
            self,
            event: TraceEvent,
            frame: FrameType,
            trace_stmt: TraceStatement
    ):
        if self._prev_stmt_done_executing(event, trace_stmt):
            stmt = self.cur_frame_last_stmt
            if stmt is not None:
                stmt.make_lhs_data_cells_if_has_lval()
                if isinstance(stmt.stmt_node, ast.ClassDef):
                    class_ref = stmt.frame.f_locals[stmt.stmt_node.name]
                    self.safety.namespaces[id(class_ref)] = self.cur_frame_scope

        self.last_code_stmt = trace_stmt
        if event == TraceEvent.line:
            self.cur_frame_last_stmt = trace_stmt
        if event == TraceEvent.call:
            self.stack.append(self.cur_frame_last_stmt)
            self.cur_frame_scope = trace_stmt.get_post_call_scope(self.cur_frame_scope)
            logging.debug('entering scope %s', self.cur_frame_scope)
            self.cur_frame_last_stmt = None
        if event == TraceEvent.return_:
            logging.debug('leaving scope %s', self.cur_frame_scope)
            ret_stmt = self.stack.pop()
            assert ret_stmt is not None
            if not isinstance(ret_stmt.stmt_node, ast.ClassDef):
                ret_stmt.extra_dependencies |= trace_stmt.compute_rval_dependencies()
            # reset 'cur_frame_last_line' for the previous frame, so that we push it again if it has another funcall
            self.cur_frame_last_stmt = ret_stmt
            self.cur_frame_scope = ret_stmt.scope
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
