# -*- coding: future_annotations -*-
import difflib
import functools
import logging
import sys
from typing import TYPE_CHECKING

import hypothesis.strategies as st
from hypothesis import example, given, settings

from nbsafety.safety import NotebookSafety
from nbsafety.tracing.trace_events import TraceEvent
from nbsafety.tracing.nbsafety_tracer import SafetyTracerStateMachine
from .utils import make_safety_fixture, skipif_known_failing

if TYPE_CHECKING:
    from typing import List, Set, Union
    from types import FrameType

logging.basicConfig(level=logging.INFO)


NotebookSafety.instance()
_ALL_EVENTS_WITH_HANDLERS = SafetyTracerStateMachine.instance().events_with_registered_handlers
_RECORDED_EVENTS = []


def subsets(draw, elements):
    return {e for e in elements if draw(st.booleans())}


def patched_emit_event_fixture():
    _RECORDED_EVENTS.clear()
    original_emit_event = SafetyTracerStateMachine._emit_event

    def _patched_emit_event(self, evt: Union[TraceEvent, str], *args, **kwargs):
        event = TraceEvent(evt) if isinstance(evt, str) else evt
        if event in _ALL_EVENTS_WITH_HANDLERS:
            frame: FrameType = kwargs.get('_frame', sys._getframe().f_back)
            kwargs['_frame'] = frame
            if frame.f_code.co_filename.startswith('<ipython-input'):
                if not (
                    (event == TraceEvent.call and self.call_depth == 0) or
                    (event == TraceEvent.return_ and self.call_depth == 1)
                ):
                    _RECORDED_EVENTS.append(event)
        return original_emit_event(self, evt, *args, **kwargs)
    SafetyTracerStateMachine._emit_event = _patched_emit_event
    yield
    SafetyTracerStateMachine._emit_event = original_emit_event


# Reset dependency graph before each test
_safety_fixture, run_cell_ = make_safety_fixture(
    extra_fixture=patched_emit_event_fixture,
    # trace_messages_enabled=True,
)


_DIFFER = difflib.Differ()


def patch_events_with_registered_handlers_to_subset(testfunc):

    @functools.wraps(testfunc)
    @settings(max_examples=20)
    @example(events=set(_ALL_EVENTS_WITH_HANDLERS))
    def wrapped_testfunc(events):
        if TraceEvent.before_call in events:
            events.add(TraceEvent.after_call)
        if TraceEvent.after_call in events:
            events.add(TraceEvent.before_call)
        if TraceEvent.subscript in events:
            events.add(TraceEvent._load_saved_slice)
        if TraceEvent._load_saved_slice in events:
            events.add(TraceEvent.subscript)
        if TraceEvent.before_list_literal in events:
            events.add(TraceEvent.after_list_literal)
        if TraceEvent.after_list_literal in events:
            events.add(TraceEvent.before_list_literal)
        if TraceEvent.before_dict_literal in events:
            events.add(TraceEvent.after_dict_literal)
        if TraceEvent.after_dict_literal in events:
            events.add(TraceEvent.before_dict_literal)
        if TraceEvent.before_tuple_literal in events:
            events.add(TraceEvent.after_tuple_literal)
        if TraceEvent.after_tuple_literal in events:
            events.add(TraceEvent.before_tuple_literal)
        original_events_with_registered_handlers = SafetyTracerStateMachine.events_with_registered_handlers

        @property
        def patched_events_with_registered_handlers(self):
            return frozenset(events)

        try:
            SafetyTracerStateMachine.events_with_registered_handlers = patched_events_with_registered_handlers
            _RECORDED_EVENTS.clear()
            testfunc(events)
        finally:
            SafetyTracerStateMachine.events_with_registered_handlers = original_events_with_registered_handlers

    return wrapped_testfunc


def filter_events_to_subset(events: List[TraceEvent], subset: Set[TraceEvent]) -> List[TraceEvent]:
    return [evt for evt in events if evt in subset]


def throw_and_print_diff_if_recorded_not_equal_to(actual: List[TraceEvent]) -> None:
    assert _RECORDED_EVENTS == actual, '\n'.join(
        _DIFFER.compare([evt.value for evt in _RECORDED_EVENTS], [evt.value for evt in actual])
    )
    _RECORDED_EVENTS.clear()


def run_cell(cell, **kwargs):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell, **kwargs)


@st.composite
def subsets(draw, elements):
    return {
        e for e in elements if draw(st.booleans())
    } | {
        TraceEvent.init_module,
        TraceEvent.before_stmt,
        TraceEvent.after_stmt,
        TraceEvent.after_module_stmt,
        TraceEvent.call,
        TraceEvent.return_,
        TraceEvent.after_for_loop_iter,
        TraceEvent.after_while_loop_iter,
    }


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_recorded_events_simple(events):
    assert _RECORDED_EVENTS == []
    run_cell('logging.info("foo")')
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset([
            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.before_complex_symbol,
            TraceEvent.attribute,
            TraceEvent.before_call,
            TraceEvent.argument,
            TraceEvent.after_call,
            TraceEvent.after_complex_symbol,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
        ], events)
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_recorded_events_two_stmts(events):
    assert _RECORDED_EVENTS == []
    run_cell('x = [1, 2, 3]')
    run_cell('logging.info(x)')
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset([
            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.before_assign_rhs,
            TraceEvent.before_list_literal,
            *([TraceEvent.list_elt] * 3),
            TraceEvent.after_list_literal,
            TraceEvent.after_assign_rhs,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,

            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.before_complex_symbol,
            TraceEvent.attribute,
            TraceEvent.before_call,
            TraceEvent.argument,
            TraceEvent.after_call,
            TraceEvent.after_complex_symbol,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
        ], events)
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_nested_chains_no_call(events):
    assert _RECORDED_EVENTS == []
    run_cell('logging.info("foo is %s", logging.info("foo"))')
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset([
            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.before_complex_symbol,
            TraceEvent.attribute,
            TraceEvent.before_call,
            TraceEvent.argument,

            # next events correspond to `logging.info("foo")`
            TraceEvent.before_complex_symbol,
            TraceEvent.attribute,
            TraceEvent.before_call,
            TraceEvent.argument,
            TraceEvent.after_call,
            TraceEvent.after_complex_symbol,
            TraceEvent.argument,

            TraceEvent.after_call,
            TraceEvent.after_complex_symbol,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
        ], events)
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_list_nested_in_dict(events):
    assert _RECORDED_EVENTS == []
    run_cell('x = {1: [2, 3, 4]}')
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset([
            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.before_assign_rhs,
            TraceEvent.before_dict_literal,

            TraceEvent.dict_key,
            TraceEvent.before_list_literal,
            *([TraceEvent.list_elt] * 3),
            TraceEvent.after_list_literal,
            TraceEvent.dict_value,

            TraceEvent.after_dict_literal,
            TraceEvent.after_assign_rhs,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
        ], events)
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_function_call(events):
    assert _RECORDED_EVENTS == []
    run_cell(
        """
        def foo(x):
            return [x]
        """
    )
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset([
            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
        ], events)
    )
    run_cell('foo([42])')
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset([
            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.before_complex_symbol,
            TraceEvent.before_call,
            TraceEvent.before_list_literal,
            TraceEvent.list_elt,
            TraceEvent.after_list_literal,
            TraceEvent.argument,
            TraceEvent.call,
            TraceEvent.before_stmt,
            TraceEvent.before_return,
            TraceEvent.before_list_literal,
            TraceEvent.list_elt,
            TraceEvent.after_list_literal,
            TraceEvent.after_return,
            TraceEvent.return_,
            TraceEvent.after_call,
            TraceEvent.after_complex_symbol,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
        ], events)
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_lambda_in_tuple(events):
    assert _RECORDED_EVENTS == []
    run_cell('x = (lambda: 42,)')
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset([
            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.before_assign_rhs,
            TraceEvent.before_tuple_literal,
            TraceEvent.before_lambda,
            TraceEvent.after_lambda,
            TraceEvent.tuple_elt,
            TraceEvent.after_tuple_literal,
            TraceEvent.after_assign_rhs,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
        ], events)
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_fancy_slices(events):
    assert _RECORDED_EVENTS == []
    run_cell(
        """
        import numpy as np
        class Foo:
            def __init__(self, x):
                self.x = x
        foo = Foo(1)
        arr = np.zeros((3, 3, 3))
        """
    )
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset([
            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,

            TraceEvent.before_stmt,
            TraceEvent.call,
            TraceEvent.before_stmt,
            TraceEvent.after_stmt,
            TraceEvent.return_,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,

            TraceEvent.before_stmt,
            TraceEvent.before_assign_rhs,
            TraceEvent.before_complex_symbol,
            TraceEvent.before_call,
            TraceEvent.argument,
            TraceEvent.call,
            TraceEvent.before_stmt,
            TraceEvent.before_assign_rhs,
            TraceEvent.after_assign_rhs,
            TraceEvent.before_complex_symbol,
            TraceEvent.attribute,
            TraceEvent.after_complex_symbol,
            TraceEvent.after_stmt,
            TraceEvent.return_,
            TraceEvent.after_call,
            TraceEvent.after_complex_symbol,
            TraceEvent.after_assign_rhs,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,

            TraceEvent.before_stmt,
            TraceEvent.before_assign_rhs,
            TraceEvent.before_complex_symbol,
            TraceEvent.attribute,
            TraceEvent.before_call,
            TraceEvent.before_tuple_literal,
            TraceEvent.tuple_elt,
            TraceEvent.tuple_elt,
            TraceEvent.tuple_elt,
            TraceEvent.after_tuple_literal,
            TraceEvent.argument,
            TraceEvent.after_call,
            TraceEvent.after_complex_symbol,
            TraceEvent.after_assign_rhs,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
        ], events)
    )

    run_cell('logging.info(arr[foo.x:foo.x+1,...])')
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset([
            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.before_complex_symbol,
            TraceEvent.attribute,
            TraceEvent.before_call,
            TraceEvent.before_complex_symbol,
            TraceEvent.before_complex_symbol,
            TraceEvent.attribute,
            TraceEvent.after_complex_symbol,
            TraceEvent.before_complex_symbol,
            TraceEvent.attribute,
            TraceEvent.after_complex_symbol,
            TraceEvent.subscript_slice,
            TraceEvent.subscript,
            TraceEvent._load_saved_slice,
            TraceEvent.after_complex_symbol,
            TraceEvent.argument,
            TraceEvent.after_call,
            TraceEvent.after_complex_symbol,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
        ], events)
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_for_loop(events):
    assert _RECORDED_EVENTS == []
    run_cell(
        """
        for i in range(10):
            pass
        """
    )
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset([
            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.before_complex_symbol,
            TraceEvent.before_call,
            TraceEvent.argument,
            TraceEvent.after_call,
            TraceEvent.after_complex_symbol,
        ] + [
            TraceEvent.before_stmt,
            TraceEvent.after_stmt,
            TraceEvent.after_for_loop_iter,
        # ] * 10 + [
        ] * 1 + [
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
        ], events)
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_while_loop(events):
    assert _RECORDED_EVENTS == []
    run_cell(
        """
        i = 0
        while i < 10:
            i += 1
        """
    )
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset([
            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.before_assign_rhs,
            TraceEvent.after_assign_rhs,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
            TraceEvent.before_stmt,
        ] + [
            TraceEvent.before_stmt,
            TraceEvent.after_stmt,
            TraceEvent.after_while_loop_iter,
        # ] * 10 + [
        ] * 1 + [
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
        ], events)
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_loop_with_continue(events):
    assert _RECORDED_EVENTS == []
    run_cell(
        """
        for i in range(10):
            continue
            print("hi")
        """
    )
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset([
            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.before_complex_symbol,
            TraceEvent.before_call,
            TraceEvent.argument,
            TraceEvent.after_call,
            TraceEvent.after_complex_symbol,
            TraceEvent.before_stmt,
            TraceEvent.after_for_loop_iter,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
        ], events)
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_for_loop_nested_in_while_loop(events):
    assert _RECORDED_EVENTS == []
    run_cell(
        """
        i = 0
        while i < 10:
            for j in range(2):
                i += 1
        """
    )
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset([
            TraceEvent.init_module,
            TraceEvent.before_stmt,
            TraceEvent.before_assign_rhs,
            TraceEvent.after_assign_rhs,
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
            TraceEvent.before_stmt,
        ] + [
            TraceEvent.before_stmt,
            TraceEvent.before_complex_symbol,
            TraceEvent.before_call,
            TraceEvent.argument,
            TraceEvent.after_call,
            TraceEvent.after_complex_symbol,

            TraceEvent.before_stmt,
            TraceEvent.after_stmt,
            TraceEvent.after_for_loop_iter,
            # TraceEvent.before_stmt,
            # TraceEvent.after_stmt,
            # TraceEvent.after_loop_iter,

            TraceEvent.after_stmt,
            TraceEvent.after_while_loop_iter,
        # ] * 5 + [
        ] * 1 + [
            TraceEvent.after_stmt,
            TraceEvent.after_module_stmt,
        ], events)
    )
