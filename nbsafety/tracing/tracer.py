# -*- coding: utf-8 -*-
from __future__ import annotations
import ast
import logging
from typing import TYPE_CHECKING

from IPython import get_ipython

from .code_line import CodeLine
from .trace_state import TraceState

if TYPE_CHECKING:
    from types import FrameType
    from ..safety import DependencySafety


def make_tracer(safety: DependencySafety, state: TraceState):
    def tracer(frame: FrameType, event: str, _):
        # this is a bit of a hack to get the class out of the locals
        # - it relies on 'self' being used... normally a safe assumption!
        try:
            class_name = frame.f_locals['self'].__class__.__name__
        except (KeyError, AttributeError):
            class_name = "No Class"

        # notebook filenames appear as 'ipython-input...'
        if 'ipython-input' not in frame.f_code.co_filename:
            return

        cell_num, lineno = TraceState.get_position(frame)
        # TODO: cache the split-by-newline operation so that we're not doing it on every instruction
        state.source = get_ipython().all_ns_refs[0]['In'][cell_num].split('\n')
        line = state.source[lineno - 1]

        # IPython quirk -- every line in outer scope apparently wrapped in lambda
        # We want to skip the outer 'call' and 'return' for these
        if event == 'call':
            state.call_depth += 1
            if state.call_depth == 1:
                return tracer

        if event == 'return':
            state.call_depth -= 1
            if state.call_depth == 0:
                return tracer

        to_parse = line = line.strip()
        logging.debug('%s %s %s %s', lineno, state.call_depth, event, line)
        if to_parse[-1] == ':':
            to_parse += '\n    pass'
        node = ast.parse(to_parse).body[0]
        code_line = state.code_lines.get(
            (cell_num, lineno), CodeLine(safety, line, node, lineno, state.cur_frame_scope)
        )
        # print(lineno, state.call_depth, event, line)
        state.code_lines[(cell_num, lineno)] = code_line
        state.update_hook(event, frame, code_line)
        return tracer
    return tracer
