# -*- coding: utf-8 -*-
import difflib
import functools
import logging
from types import FrameType
from typing import List, Set, Union

import hypothesis.strategies as st
from hypothesis import example, given, settings
from pyccolo import TraceEvent

from ipyflow.flow import NotebookFlow
from ipyflow.singletons import tracer
from ipyflow.tracing.ipyflow_tracer import DataflowTracer

from .utils import make_flow_fixture, skipif_known_failing

logging.basicConfig(level=logging.INFO)


NotebookFlow.instance()
_ALL_EVENTS_WITH_HANDLERS = DataflowTracer.instance().events_with_registered_handlers
_RECORDED_EVENTS = []


def subsets(draw, elements):
    return {e for e in elements if draw(st.booleans())}


def patched_emit_event_fixture():
    _RECORDED_EVENTS.clear()
    original_emit_event = DataflowTracer._emit_event

    def _patched_emit_event(
        self, evt: Union[str, TraceEvent], node_id: int, frame: FrameType, **kwargs
    ):
        event = evt if isinstance(evt, TraceEvent) else TraceEvent(evt)
        if frame is not None and frame.f_code.co_filename.startswith("<ipython-input"):
            is_traced_lambda = frame.f_code.co_name == "<traced_lambda>"
            if not (
                (
                    event == TraceEvent.call
                    and (self.call_depth == 0 or is_traced_lambda)
                )
                or (
                    event == TraceEvent.return_
                    and (self.call_depth == 1 or is_traced_lambda)
                )
            ):
                if event in self.events_with_registered_handlers:
                    _RECORDED_EVENTS.append(event)
        return original_emit_event(self, event, node_id, frame, **kwargs)

    DataflowTracer._emit_event = _patched_emit_event
    yield
    DataflowTracer._emit_event = original_emit_event


# Reset dependency graph before each test
_flow_fixture, run_cell_ = make_flow_fixture(
    extra_fixture=patched_emit_event_fixture,
    # trace_messages_enabled=True,
)


_DIFFER = difflib.Differ()


def patch_events_with_registered_handlers_to_subset(testfunc):
    @functools.wraps(testfunc)
    @settings(max_examples=20, deadline=None)
    @example(events=set(_ALL_EVENTS_WITH_HANDLERS))
    def wrapped_testfunc(events):
        events |= {
            TraceEvent.before_subscript_load,
            TraceEvent.after_subscript_load,
            TraceEvent.before_subscript_store,
            TraceEvent.before_subscript_del,
            TraceEvent._load_saved_slice,
            TraceEvent.before_load_complex_symbol,
            TraceEvent.after_load_complex_symbol,
            TraceEvent.before_attribute_load,
            TraceEvent.after_attribute_load,
            TraceEvent.before_attribute_store,
            TraceEvent.before_attribute_del,
            TraceEvent.before_call,
            TraceEvent.after_call,
            TraceEvent.after_argument,
            TraceEvent.before_return,
            TraceEvent.after_return,
            TraceEvent.call,
            TraceEvent.return_,
            TraceEvent.exception,
            TraceEvent.before_stmt,
            TraceEvent.after_stmt,
            TraceEvent.after_assign_rhs,
        }
        list_literal_related = {
            TraceEvent.before_list_literal,
            TraceEvent.after_list_literal,
            TraceEvent.list_elt,
        }
        if events & list_literal_related:
            events |= list_literal_related
        set_literal_related = {
            TraceEvent.before_set_literal,
            TraceEvent.after_set_literal,
            TraceEvent.set_elt,
        }
        if events & set_literal_related:
            events |= set_literal_related
        tuple_literal_related = {
            TraceEvent.before_tuple_literal,
            TraceEvent.after_tuple_literal,
            TraceEvent.tuple_elt,
        }
        if events & tuple_literal_related:
            events |= tuple_literal_related
        dict_literal_related = {
            TraceEvent.before_dict_literal,
            TraceEvent.after_dict_literal,
            TraceEvent.dict_key,
            TraceEvent.dict_value,
        }
        if events & dict_literal_related:
            events |= dict_literal_related

        orig_handlers = tracer().events_with_registered_handlers
        try:
            tracer().events_with_registered_handlers = frozenset(events)
            _RECORDED_EVENTS.clear()
            testfunc(events)
        finally:
            tracer().events_with_registered_handlers = orig_handlers

    return wrapped_testfunc


def filter_events_to_subset(
    events: List[TraceEvent], subset: Set[TraceEvent]
) -> List[TraceEvent]:
    return [evt for evt in events if evt in subset]


def throw_and_print_diff_if_recorded_not_equal_to(actual: List[TraceEvent]) -> None:
    assert _RECORDED_EVENTS == actual, "\n".join(
        _DIFFER.compare(
            [evt.value for evt in _RECORDED_EVENTS], [evt.value for evt in actual]
        )
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
    return {e for e in elements if draw(st.booleans())} | {
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
        filter_events_to_subset(
            [
                TraceEvent.init_module,
                TraceEvent.before_stmt,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_attribute_load,
                TraceEvent.after_attribute_load,
                TraceEvent.before_call,
                TraceEvent.after_argument,
                TraceEvent.after_call,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
            ],
            events,
        )
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_recorded_events_two_stmts(events):
    assert _RECORDED_EVENTS == []
    run_cell("x = [1, 2, 3]")
    run_cell("logging.info(x)")
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset(
            [
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
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_attribute_load,
                TraceEvent.after_attribute_load,
                TraceEvent.before_call,
                TraceEvent.load_name,
                TraceEvent.after_argument,
                TraceEvent.after_call,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
            ],
            events,
        )
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_nested_chains_no_call(events):
    assert _RECORDED_EVENTS == []
    run_cell('logging.info("foo is %s", logging.info("foo"))')
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset(
            [
                TraceEvent.init_module,
                TraceEvent.before_stmt,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_attribute_load,
                TraceEvent.after_attribute_load,
                TraceEvent.before_call,
                TraceEvent.after_argument,
                # next events correspond to `logging.info("foo")`
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_attribute_load,
                TraceEvent.after_attribute_load,
                TraceEvent.before_call,
                TraceEvent.after_argument,
                TraceEvent.after_call,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.after_argument,
                TraceEvent.after_call,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
            ],
            events,
        )
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_list_nested_in_dict(events):
    assert _RECORDED_EVENTS == []
    run_cell("x = {1: [2, 3, 4]}")
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset(
            [
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
            ],
            events,
        )
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
        filter_events_to_subset(
            [
                TraceEvent.init_module,
                TraceEvent.before_stmt,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
            ],
            events,
        )
    )
    run_cell("foo([42])")
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset(
            [
                TraceEvent.init_module,
                TraceEvent.before_stmt,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_call,
                TraceEvent.before_list_literal,
                TraceEvent.list_elt,
                TraceEvent.after_list_literal,
                TraceEvent.after_argument,
                TraceEvent.call,
                TraceEvent.before_function_body,
                TraceEvent.before_stmt,
                TraceEvent.before_return,
                TraceEvent.before_list_literal,
                TraceEvent.load_name,
                TraceEvent.list_elt,
                TraceEvent.after_list_literal,
                TraceEvent.after_return,
                TraceEvent.after_function_execution,
                TraceEvent.return_,
                TraceEvent.after_call,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
            ],
            events,
        )
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_lambda_in_tuple(events):
    assert _RECORDED_EVENTS == []
    run_cell("x = (lambda: 42,)")
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset(
            [
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
            ],
            events,
        )
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
        filter_events_to_subset(
            [
                # import numpy as np
                TraceEvent.init_module,
                TraceEvent.before_stmt,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
                # class Foo: ...
                TraceEvent.before_stmt,
                TraceEvent.call,
                TraceEvent.before_stmt,
                TraceEvent.after_stmt,
                TraceEvent.return_,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
                # foo = Foo(1)
                TraceEvent.before_stmt,
                TraceEvent.before_assign_rhs,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_call,
                TraceEvent.after_argument,
                TraceEvent.call,
                TraceEvent.before_function_body,
                TraceEvent.before_stmt,
                TraceEvent.before_assign_rhs,
                TraceEvent.load_name,
                TraceEvent.after_assign_rhs,
                TraceEvent.load_name,
                TraceEvent.before_attribute_store,
                TraceEvent.after_stmt,
                TraceEvent.after_function_execution,
                TraceEvent.return_,
                TraceEvent.after_call,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.after_assign_rhs,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
                # arr = np.zeros((3, 3, 3))
                TraceEvent.before_stmt,
                TraceEvent.before_assign_rhs,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_attribute_load,
                TraceEvent.after_attribute_load,
                TraceEvent.before_call,
                TraceEvent.before_tuple_literal,
                TraceEvent.tuple_elt,
                TraceEvent.tuple_elt,
                TraceEvent.tuple_elt,
                TraceEvent.after_tuple_literal,
                TraceEvent.after_argument,
                TraceEvent.after_call,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.after_assign_rhs,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
            ],
            events,
        )
    )

    run_cell("logging.info(arr[foo.x:foo.x+1,...])")
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset(
            [
                TraceEvent.init_module,
                TraceEvent.before_stmt,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_attribute_load,
                TraceEvent.after_attribute_load,
                TraceEvent.before_call,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_attribute_load,
                TraceEvent.after_attribute_load,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_attribute_load,
                TraceEvent.after_attribute_load,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.after_subscript_slice,
                TraceEvent.before_subscript_load,
                TraceEvent._load_saved_slice,
                TraceEvent.after_subscript_load,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.after_argument,
                TraceEvent.after_call,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
            ],
            events,
        )
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
        filter_events_to_subset(
            [
                TraceEvent.init_module,
                TraceEvent.before_stmt,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_call,
                TraceEvent.after_argument,
                TraceEvent.after_call,
                TraceEvent.after_load_complex_symbol,
            ]
            + [
                TraceEvent.before_for_loop_body,
                TraceEvent.before_stmt,
                TraceEvent.after_stmt,
                TraceEvent.after_for_loop_iter,
                # ] * 10 + [
            ]
            * 1
            + [
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
            ],
            events,
        )
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
        filter_events_to_subset(
            [
                TraceEvent.init_module,
                TraceEvent.before_stmt,
                TraceEvent.before_assign_rhs,
                TraceEvent.after_assign_rhs,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
                TraceEvent.before_stmt,
            ]
            + [
                TraceEvent.load_name,
                TraceEvent.before_while_loop_body,
                TraceEvent.before_stmt,
                TraceEvent.after_stmt,
                TraceEvent.after_while_loop_iter,
                # ] * 10 + [
            ]
            * 1
            + [
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
            ],
            events,
        )
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
        filter_events_to_subset(
            [
                TraceEvent.init_module,
                TraceEvent.before_stmt,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_call,
                TraceEvent.after_argument,
                TraceEvent.after_call,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.before_for_loop_body,
                TraceEvent.before_stmt,
                TraceEvent.after_for_loop_iter,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
            ],
            events,
        )
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
        filter_events_to_subset(
            [
                TraceEvent.init_module,
                TraceEvent.before_stmt,
                TraceEvent.before_assign_rhs,
                TraceEvent.after_assign_rhs,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
                TraceEvent.before_stmt,
            ]
            + [
                TraceEvent.load_name,
                TraceEvent.before_while_loop_body,
                TraceEvent.before_stmt,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_call,
                TraceEvent.after_argument,
                TraceEvent.after_call,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.before_for_loop_body,
                TraceEvent.before_stmt,
                TraceEvent.after_stmt,
                TraceEvent.after_for_loop_iter,
                # TraceEvent.before_stmt,
                # TraceEvent.after_stmt,
                # TraceEvent.after_loop_iter,
                TraceEvent.after_stmt,
                TraceEvent.after_while_loop_iter,
                # ] * 5 + [
            ]
            * 1
            + [
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
            ],
            events,
        )
    )


@given(events=subsets(_ALL_EVENTS_WITH_HANDLERS))
@patch_events_with_registered_handlers_to_subset
def test_lambda_wrapping_call(events):
    assert _RECORDED_EVENTS == []
    run_cell(
        """
        z = 42
        def f():
            return z
        lam = lambda: f()
        x = lam()
        """
    )
    throw_and_print_diff_if_recorded_not_equal_to(
        filter_events_to_subset(
            [
                TraceEvent.init_module,
                # z = 42
                TraceEvent.before_stmt,
                TraceEvent.after_assign_rhs,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
                # def f(): ...
                TraceEvent.before_stmt,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
                # lam = lambda: f()
                TraceEvent.before_stmt,
                TraceEvent.before_lambda,
                TraceEvent.after_lambda,
                TraceEvent.after_assign_rhs,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
                # x = lam()
                TraceEvent.before_stmt,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_call,
                TraceEvent.call,
                TraceEvent.before_lambda_body,
                TraceEvent.before_load_complex_symbol,
                TraceEvent.load_name,
                TraceEvent.before_call,
                TraceEvent.call,
                TraceEvent.before_function_body,
                TraceEvent.before_stmt,
                TraceEvent.before_return,
                TraceEvent.load_name,
                TraceEvent.after_return,
                TraceEvent.after_function_execution,
                TraceEvent.return_,
                TraceEvent.after_call,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.return_,
                TraceEvent.after_call,
                TraceEvent.after_load_complex_symbol,
                TraceEvent.after_assign_rhs,
                TraceEvent.after_stmt,
                TraceEvent.after_module_stmt,
            ],
            events,
        )
    )
