# -*- coding: future_annotations -*-
import logging
import sys
from typing import TYPE_CHECKING

from nbsafety.tracing.trace_events import TraceEvent
from nbsafety.tracing.trace_manager import TraceManager
from .utils import make_safety_fixture

if TYPE_CHECKING:
    from typing import Union
    from types import FrameType

logging.basicConfig(level=logging.INFO)


RECORDED_EVENTS = []


def patched_emit_event_fixture():
    RECORDED_EVENTS.clear()
    original_emit_event = TraceManager._emit_event

    def _patched_emit_event(self, evt: Union[TraceEvent, str], *args, **kwargs):
        event = TraceEvent(evt) if isinstance(evt, str) else evt
        frame: FrameType = kwargs.get('_frame', sys._getframe().f_back)
        kwargs['_frame'] = frame
        if frame.f_code.co_filename.startswith('<ipython-input'):
            if not ((event == TraceEvent.call and self.call_depth == 0) or (event == TraceEvent.return_ and self.call_depth == 1)):
                RECORDED_EVENTS.append(event)
        return original_emit_event(self, evt, *args, **kwargs)
    TraceManager._emit_event = _patched_emit_event
    yield
    TraceManager._emit_event = original_emit_event


# Reset dependency graph before each test
_safety_fixture, run_cell_ = make_safety_fixture(
    extra_fixture=patched_emit_event_fixture,
    trace_messages_enabled=True,
)


def run_cell(cell, **kwargs):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell, **kwargs)


def test_recorded_events_simple():
    assert RECORDED_EVENTS == []
    run_cell('logging.info("foo")')
    assert RECORDED_EVENTS == [
        TraceEvent.before_stmt,
        TraceEvent.before_complex_symbol,
        TraceEvent.attribute,
        TraceEvent.before_arg_list,
        TraceEvent.argument,
        TraceEvent.after_arg_list,
        TraceEvent.after_complex_symbol,
        TraceEvent.after_stmt
    ], 'unexpected events; got %s' % RECORDED_EVENTS


def test_recorded_events_two_stmts():
    assert RECORDED_EVENTS == []
    run_cell('x = [1, 2, 3]')
    run_cell('logging.info(x)')
    assert RECORDED_EVENTS == [
        TraceEvent.before_stmt,
        TraceEvent.before_literal,
        TraceEvent.after_literal,
        TraceEvent.after_stmt,

        TraceEvent.before_stmt,
        TraceEvent.before_complex_symbol,
        TraceEvent.attribute,
        TraceEvent.before_arg_list,
        TraceEvent.argument,
        TraceEvent.after_arg_list,
        TraceEvent.after_complex_symbol,
        TraceEvent.after_stmt
    ], 'unexpected events; got %s' % RECORDED_EVENTS


def test_nested_chains_no_call():
    assert RECORDED_EVENTS == []
    run_cell('logging.info("foo is %s", logging.info("foo"))')
    assert RECORDED_EVENTS == [
        TraceEvent.before_stmt,
        TraceEvent.before_complex_symbol,
        TraceEvent.attribute,
        TraceEvent.before_arg_list,
        TraceEvent.argument,

        # next events correspond to `logging.info("foo")`
        TraceEvent.before_complex_symbol,
        TraceEvent.attribute,
        TraceEvent.before_arg_list,
        TraceEvent.argument,
        TraceEvent.after_arg_list,
        TraceEvent.after_complex_symbol,
        TraceEvent.argument,

        TraceEvent.after_arg_list,
        TraceEvent.after_complex_symbol,
        TraceEvent.after_stmt
    ], 'unexpected events; got %s' % RECORDED_EVENTS
