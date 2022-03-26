# -*- coding: utf-8 -*-
import textwrap

from ipyflow.data_model.code_cell import cells

from ipyflow.line_magics import _USAGE
from ipyflow.run_mode import FlowOrder, ExecutionMode, ExecutionSchedule
from ipyflow.singletons import kernel, flow
from ipyflow.tracing.ipyflow_tracer import SafetyTracer
from test.utils import make_safety_fixture

# Reset dependency graph before each test
_safety_fixture, run_cell_ = make_safety_fixture()


def run_cell(cell, **kwargs):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell, **kwargs)


def test_show_usage():
    run_cell("%safety not_a_real_subcommand")
    cell1 = cells().from_id(1)
    assert cell1.captured_output.stderr.strip() == _USAGE.strip(), (
        "got %s" % cell1.captured_output.stderr
    )


def test_show_deps_show_stale():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell("logging.info(y)")
    run_cell("%safety show_deps y")
    cell4 = cells().from_id(4)
    assert (
        cell4.captured_output.stdout.strip()
        == "Symbol y (defined cell: 2; last updated cell: 2) is dependent on {<x>} and is a parent of nothing"
    ), ("got %s" % cell4.captured_output)
    run_cell("%safety show_stale")
    cell5 = cells().from_id(5)
    assert (
        cell5.captured_output.stdout.strip()
        == "No symbol has stale dependencies for now!"
    ), ("got %s" % cell5.captured_output)
    run_cell("x = 42")
    run_cell("%safety show_stale")
    cell7 = cells().from_id(7)
    assert (
        cell7.captured_output.stdout.strip()
        == "Symbol(s) with stale dependencies: {<y>}"
    ), ("got %s" % cell7.captured_output)
    run_cell("y = x + 1")
    run_cell("%safety show_stale")
    cell9 = cells().from_id(9)
    assert (
        cell9.captured_output.stdout.strip()
        == "No symbol has stale dependencies for now!"
    ), ("got %s" % cell9.captured_output)


def test_enable_disable_trace_messages():
    assert not flow().trace_messages_enabled
    run_cell("%safety trace_messages enable")
    assert flow().trace_messages_enabled
    run_cell("%safety trace_messages disable")
    assert not flow().trace_messages_enabled


def test_enable_disable_highlights():
    assert flow().mut_settings.highlights_enabled
    run_cell("%safety nohls")
    assert not flow().mut_settings.highlights_enabled
    run_cell("%safety hls")
    assert flow().mut_settings.highlights_enabled
    run_cell("%safety highlights off")
    assert not flow().mut_settings.highlights_enabled
    run_cell("%safety highlights on")
    assert flow().mut_settings.highlights_enabled
    run_cell("%safety highlights disable")
    assert not flow().mut_settings.highlights_enabled
    run_cell("%safety highlights enable")


def test_make_slice():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell("x = 42")
    run_cell("logging.info(y)")
    run_cell("%safety slice 4")
    cell5 = cells().from_id(5)
    assert (
        cell5.captured_output.stdout.strip()
        == textwrap.dedent(
            """
        # Cell 1
        x = 0
        
        # Cell 2
        y = x + 1
        
        # Cell 4
        logging.info(y)
        """
        ).strip()
    ), ("got %s" % cell5.captured_output)


def test_set_exec_mode():
    assert flow().mut_settings.exec_mode == ExecutionMode.NORMAL
    run_cell(f"%safety mode {ExecutionMode.REACTIVE.value}")
    assert flow().mut_settings.exec_mode == ExecutionMode.REACTIVE
    run_cell(f"%safety mode {ExecutionMode.NORMAL.value}")
    assert flow().mut_settings.exec_mode == ExecutionMode.NORMAL


def test_set_exec_schedule_and_flow_order():
    assert flow().mut_settings.exec_schedule == ExecutionSchedule.LIVENESS_BASED
    run_cell(f"%safety flow {FlowOrder.IN_ORDER.value}")
    assert flow().mut_settings.flow_order == FlowOrder.IN_ORDER
    for schedule in ExecutionSchedule:
        run_cell(f"%safety schedule {schedule.value}")
        assert flow().mut_settings.exec_schedule == schedule
    run_cell(f"%safety schedule {ExecutionSchedule.LIVENESS_BASED.value}")
    assert flow().mut_settings.exec_schedule == ExecutionSchedule.LIVENESS_BASED
    run_cell(f"%safety flow {FlowOrder.ANY_ORDER.value}")
    assert flow().mut_settings.flow_order == FlowOrder.ANY_ORDER
    run_cell(f"%safety schedule {ExecutionSchedule.STRICT.value}")
    # strict schedule only works for in_order semantics
    assert flow().mut_settings.exec_schedule == ExecutionSchedule.LIVENESS_BASED


def test_register_deregister_tracer():
    assert SafetyTracer in kernel().registered_tracers
    run_cell(f"%safety deregister {SafetyTracer.__module__}.{SafetyTracer.__name__}")
    assert SafetyTracer not in kernel().registered_tracers
    run_cell(f"%safety register {SafetyTracer.__module__}.{SafetyTracer.__name__}")
    assert SafetyTracer in kernel().registered_tracers


def test_clear():
    run_cell("%safety clear")
    assert flow().min_timestamp == flow().cell_counter()
    run_cell("x = 42")
    assert flow().min_timestamp == flow().cell_counter() - 1
    run_cell("%safety clear")
    assert flow().min_timestamp == flow().cell_counter()
