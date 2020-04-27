# -*- coding: utf-8 -*-
from __future__ import annotations
import ast

from IPython import get_ipython

from .code_line import CodeLine


def make_tracer(safety, state):
    def tracer(frame, event, _):
        # this is a bit of a hack to get the class out of the locals
        # - it relies on 'self' being used... normally a safe assumption!
        try:
            class_name = frame.f_locals['self'].__class__.__name__
        except (KeyError, AttributeError):
            class_name = "No Class"

        # notebook filenames appear as 'ipython-input...'
        if 'ipython-input' not in frame.f_code.co_filename:
            return

        # if state.source is None:
        # state.source = inspect.getsource(frame).split('\n')
        # try:
        #     line = state.source[frame.f_lineno-1]
        # except:
        #     print(inspect.getsource(frame))
        #     print(frame.f_lineno)
        #     raise
        cell_num = int(frame.f_code.co_filename.split('-')[2])
        state.source = get_ipython().all_ns_refs[0]['In'][cell_num].split('\n')
        line = state.source[frame.f_lineno - 1]

        old_depth = state.call_depth

        # IPython quirk -- every line in outer scope apparently wrapped in lambda
        # We want to skip the outer 'call' and 'return' for these
        if event == 'call':
            state.call_depth += 1
            if old_depth == 0:
                return tracer

        if event == 'return':
            state.call_depth -= 1
            if old_depth <= 1:
                return tracer

        to_parse = line = line.strip()
        lineno = frame.f_lineno
        # print(lineno, state.call_depth, event, line)
        if to_parse[-1] == ':':
            to_parse += '\n    pass'
        node = ast.parse(to_parse).body[0]
        code_line = state.code_lines.get(
            lineno, CodeLine(safety, line, node, lineno, state.call_depth, frame)
        )
        state.code_lines[lineno] = code_line

        state.post_line_hook_for_event(event, safety)

        if event == 'line':
            state.cur_frame_last_line = code_line
        if event == 'call':
            state.stack.append(state.cur_frame_last_line)
            state.cur_frame_last_line = None
        if event == 'return':
            ret_line = state.stack.pop()
            assert ret_line is not None
            # reset 'cur_frame_last_line' for the previous frame, so that we push it again if it has another funcall
            state.cur_frame_last_line = ret_line
            # print('{} @@returning to@@ {}'.format(code_line.text, ret_line.text))
            ret_line.extra_dependencies |= code_line.compute_rval_dependencies()
        state.last_event = event
        return tracer
    return tracer
