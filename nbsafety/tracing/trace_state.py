# -*- coding: utf-8 -*-
from __future__ import annotations
import ast
import logging
from typing import TYPE_CHECKING

from .trace_events import TraceEvent

if TYPE_CHECKING:
    from typing import Optional, Dict, List
    from types import FrameType
    from .code_stmt import CodeStatement
    from ..scope import Scope


class TraceState(object):
    def __init__(self, cur_frame_scope: Scope):
        self.cur_frame_scope = cur_frame_scope
        self.cur_frame_last_stmt: Optional[CodeStatement] = None
        self.call_depth = 0
        self.code_statements: Dict[int, CodeStatement] = {}
        self.stack: List[CodeStatement] = []
        self.source: Optional[str] = None
        self.last_code_stmt: Optional[CodeStatement] = None
        self.last_event: Optional[TraceEvent] = None

    def _prev_stmt_done_executing(self, event: TraceEvent, code_stmt: CodeStatement):
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
            code_stmt: CodeStatement
    ):
        if self._prev_stmt_done_executing(event, code_stmt):
            stmt = self.cur_frame_last_stmt
            if stmt is not None:
                stmt.make_lhs_data_cells_if_has_lval()

        self.last_code_stmt = code_stmt
        if event == TraceEvent.line:
            self.cur_frame_last_stmt = code_stmt
        if event == TraceEvent.call:
            self.stack.append(self.cur_frame_last_stmt)
            self.cur_frame_scope = code_stmt.get_post_call_scope(self.cur_frame_scope)
            logging.debug('entering scope %s', self.cur_frame_scope)
            self.cur_frame_last_stmt = None
        if event == TraceEvent.return_:
            logging.debug('leaving scope %s', self.cur_frame_scope)
            ret_stmt = self.stack.pop()
            assert ret_stmt is not None
            ret_stmt.extra_dependencies |= code_stmt.compute_rval_dependencies()
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
