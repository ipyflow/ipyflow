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


def make_tracer(safety: DependencySafety):
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

        state = safety.trace_state  # we'll be using this a lot

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

        if state.last_event == 'exception':
            # TODO: unwind the stack
            pass

        to_parse = line = line.strip()
        print(lineno, state.call_depth, event, line)
        logging.debug('%s %s %s %s', lineno, state.call_depth, event, line)
        if line in ('try:', 'except:'):
            return tracer
        if to_parse[-1] == ':':
            to_parse += '\n    pass'
        try:
            node = ast.parse(to_parse).body[0]
        except SyntaxError:
            logging.error('got syntax error when parsing %s', to_parse)
            return tracer
        code_line = state.code_lines.get(
            (cell_num, lineno),
            CodeLine(safety, line, node, lineno, state.cur_frame_scope)
        )
        state.code_lines[(cell_num, lineno)] = code_line
        state.update_hook(event, frame, code_line)
        return tracer
    return tracer
