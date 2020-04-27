# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Optional, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .code_line import CodeLine
    from ..safety import DependencySafety


class TraceState(object):
    def __init__(self):
        self.call_depth = 0
        self.code_lines: Dict[str, CodeLine] = {}
        self.stack = []
        self.source: Optional[str] = None
        self.cur_frame_last_line: Optional[CodeLine] = None
        self.last_event: Optional[str] = None

    def post_line_hook_for_event(self, event: str, safety: DependencySafety):
        if event not in ('line', 'return') or self.last_event == 'call':
            return
        # this means that the previous line on the current frame is done executing
        line = self.cur_frame_last_line
        if line is not None and line.has_lval:
            line.make_lhs_data_cells()
