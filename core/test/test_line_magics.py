# -*- coding: utf-8 -*-
import os.path
import sys
import textwrap
from test.utils import clear_registered_annotations, make_flow_fixture

from ipyflow.annotations.compiler import (
    REGISTERED_CLASS_SPECS,
    REGISTERED_FUNCTION_SPECS,
)
from ipyflow.config import ExecutionMode, ExecutionSchedule, FlowDirection, Highlights
from ipyflow.data_model.code_cell import cells
from ipyflow.line_magics import _USAGE
from ipyflow.singletons import flow, kernel
from ipyflow.tracing.ipyflow_tracer import DataflowTracer

# Reset dependency graph before each test
_flow_fixture, run_cell_ = make_flow_fixture()


def run_cell(cell, **kwargs):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell, **kwargs)


def test_show_usage():
    run_cell("%flow not_a_real_subcommand")
    cell1 = cells().from_id(1)
    assert cell1.captured_output.stderr.strip() == _USAGE.strip(), (
        "got %s" % cell1.captured_output.stderr
    )


def test_toggle_dataflow():
    assert flow().mut_settings.dataflow_enabled
    run_cell("%flow disable")
    assert not flow().mut_settings.dataflow_enabled
    run_cell("%flow enable")
    assert flow().mut_settings.dataflow_enabled


def test_show_deps_show_waiting():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell("logging.info(y)")
    run_cell("%flow show_deps y")
    cell4 = cells().from_id(4)
    assert (
        cell4.captured_output.stdout.strip()
        == "Symbol y (defined cell: 2; last updated cell: 2) is dependent on {<x>} and is a parent of nothing"
    ), ("got %s" % cell4.captured_output)
    run_cell("%flow show_waiting")
    cell5 = cells().from_id(5)
    assert (
        cell5.captured_output.stdout.strip()
        == "No symbol waiting on dependencies for now!"
    ), ("got %s" % cell5.captured_output)
    run_cell("x = 42")
    run_cell("%flow show_waiting")
    cell7 = cells().from_id(7)
    assert (
        cell7.captured_output.stdout.strip()
        == "Symbol(s) waiting on dependencies: {<y>}"
    ), ("got %s" % cell7.captured_output)
    run_cell("y = x + 1")
    run_cell("%flow show_waiting")
    cell9 = cells().from_id(9)
    assert (
        cell9.captured_output.stdout.strip()
        == "No symbol waiting on dependencies for now!"
    ), ("got %s" % cell9.captured_output)


def test_get_code():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell("%flow get_code y")
    cell3 = cells().from_id(3)
    assert (
        cell3.captured_output.stdout.strip() == "# Cell 1\nx = 0\n\n# Cell 2\ny = x + 1"
    ), ("got %s" % cell3.captured_output)


def test_enable_disable_trace_messages():
    assert not flow().trace_messages_enabled
    run_cell("%flow trace_messages enable")
    assert flow().trace_messages_enabled
    run_cell("%flow trace_messages disable")
    assert not flow().trace_messages_enabled


def test_enable_disable_highlights():
    assert flow().mut_settings.highlights == Highlights.EXECUTED
    run_cell("%flow nohls")
    assert flow().mut_settings.highlights == Highlights.NONE
    run_cell("%flow hls")
    assert flow().mut_settings.highlights == Highlights.EXECUTED
    run_cell("%flow highlights off")
    assert flow().mut_settings.highlights == Highlights.NONE
    run_cell("%flow highlights on")
    assert flow().mut_settings.highlights == Highlights.EXECUTED
    run_cell("%flow highlights disable")
    assert flow().mut_settings.highlights == Highlights.NONE
    run_cell("%flow highlights enable")


def test_make_slice():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell("x = 42")
    run_cell("logging.info(y)")
    run_cell("%flow slice 4")
    cell5 = cells().from_id(5)
    slice_text = "\n".join(
        line for line in cell5.captured_output.stdout.splitlines() if line
    )
    expected = textwrap.dedent(
        """
        # Cell 1
        x = 0
        # Cell 2
        y = x + 1
        # Cell 4
        logging.info(y)
        """
    ).strip()
    assert slice_text == expected, "got %s instead of %s" % (slice_text, expected)


def test_set_exec_mode():
    assert flow().mut_settings.exec_mode == ExecutionMode.NORMAL
    run_cell(f"%flow mode {ExecutionMode.REACTIVE.value}")
    assert flow().mut_settings.exec_mode == ExecutionMode.REACTIVE
    run_cell(f"%flow mode {ExecutionMode.NORMAL.value}")
    assert flow().mut_settings.exec_mode == ExecutionMode.NORMAL


def test_set_exec_schedule_and_flow_order():
    assert flow().mut_settings.exec_schedule == ExecutionSchedule.LIVENESS_BASED
    run_cell(f"%flow direction {FlowDirection.IN_ORDER.value}")
    assert flow().mut_settings.flow_order == FlowDirection.IN_ORDER
    for schedule in ExecutionSchedule:
        run_cell(f"%flow schedule {schedule.value}")
        assert flow().mut_settings.exec_schedule == schedule
    run_cell(f"%flow schedule {ExecutionSchedule.LIVENESS_BASED.value}")
    assert flow().mut_settings.exec_schedule == ExecutionSchedule.LIVENESS_BASED
    run_cell(f"%flow direction {FlowDirection.ANY_ORDER.value}")
    assert flow().mut_settings.flow_order == FlowDirection.ANY_ORDER
    run_cell(f"%flow schedule {ExecutionSchedule.STRICT.value}")
    # strict schedule only works for in_order semantics
    assert flow().mut_settings.exec_schedule == ExecutionSchedule.LIVENESS_BASED


def test_register_deregister_tracer():
    assert DataflowTracer in kernel().registered_tracers
    run_cell(f"%flow deregister {DataflowTracer.__module__}.{DataflowTracer.__name__}")
    assert DataflowTracer not in kernel().registered_tracers
    run_cell(f"%flow register {DataflowTracer.__module__}.{DataflowTracer.__name__}")
    assert DataflowTracer in kernel().registered_tracers


def test_clear():
    run_cell("%flow clear")
    assert flow().min_timestamp == flow().cell_counter()
    run_cell("x = 42")
    assert flow().min_timestamp == flow().cell_counter() - 1
    run_cell("%flow clear")
    assert flow().min_timestamp == flow().cell_counter()


def test_tags():
    run_cell("%flow tag foo")
    cell1 = cells().current_cell()
    assert cell1 is cells().from_counter(1)
    assert cell1.tags == ("foo",)
    run_cell("%flow show_tags --cell 1")
    assert (
        cells().current_cell().captured_output.stdout.strip()
        == "Cell has tags: ('foo',)"
    )
    run_cell("%flow tag --remove foo --cell 1")
    assert cells().from_counter(1).tags == ()
    run_cell("%flow show-tags --cell 1")
    assert cells().current_cell().captured_output.stdout.strip() == "Cell has tags: ()"


def test_warn_out_of_order():
    assert not flow().mut_settings.warn_out_of_order_usages
    run_cell("%flow warn-ooo")
    assert flow().mut_settings.warn_out_of_order_usages
    run_cell("%flow no-warn-ooo")
    assert not flow().mut_settings.warn_out_of_order_usages


def test_lint_out_of_order():
    assert not flow().mut_settings.lint_out_of_order_usages
    run_cell("%flow lint-ooo")
    assert flow().mut_settings.lint_out_of_order_usages
    run_cell("%flow no-lint-ooo")
    assert not flow().mut_settings.lint_out_of_order_usages


def test_syntax_transforms_enabled():
    assert flow().mut_settings.syntax_transforms_enabled == (sys.version_info >= (3, 8))
    prev_enabled = flow().mut_settings.syntax_transforms_enabled
    run_cell("%flow syntax_transforms foo")
    assert flow().mut_settings.syntax_transforms_enabled == prev_enabled
    run_cell("%flow syntax_transforms off")
    assert not flow().mut_settings.syntax_transforms_enabled
    run_cell("%flow syntax_transforms on")
    assert flow().mut_settings.syntax_transforms_enabled
    if sys.version_info < (3, 8):
        run_cell("%flow syntax_transforms off")


def test_syntax_transforms_only():
    assert not flow().mut_settings.syntax_transforms_only
    run_cell("%flow syntax_transforms_only")
    assert flow().mut_settings.syntax_transforms_only
    run_cell("%flow on")
    assert not flow().mut_settings.syntax_transforms_only


def test_annotation_registration():
    with clear_registered_annotations():
        assert len(REGISTERED_CLASS_SPECS) == 0
        assert len(REGISTERED_FUNCTION_SPECS) == 0
        run_cell(f"%flow register_annotations {os.path.dirname(__file__)}")
        assert len(REGISTERED_CLASS_SPECS) > 0
        assert len(REGISTERED_FUNCTION_SPECS) > 0
