# -*- coding: utf-8 -*-
import inspect
import os
import sys
import textwrap
from contextlib import contextmanager
from typing import Any, Tuple

import pytest
from pyccolo.tracer import PYCCOLO_DEV_MODE_ENV_VAR

from ipyflow.annotations.compiler import (
    REGISTERED_CLASS_SPECS,
    REGISTERED_FUNCTION_SPECS,
)
from ipyflow.config import FlowDirection
from ipyflow.data_model.cell import cells
from ipyflow.data_model.symbol import Symbol
from ipyflow.flow import NotebookFlow
from ipyflow.shell import IPyflowInteractiveShell
from ipyflow.singletons import flow, shell
from ipyflow.tracing.external_calls.base_handlers import REGISTERED_HANDLER_BY_FUNCTION
from ipyflow.tracing.ipyflow_tracer import DataflowTracer


def should_skip_known_failing(reason="this test tests unimpled functionality"):
    return {
        "condition": bool(int(os.getenv("SHOULD_SKIP_KNOWN_FAILING", "1"))),
        "reason": reason,
    }


skipif_known_failing = pytest.mark.skipif(**should_skip_known_failing())


def assert_bool(val, msg=""):
    assert val, str(msg)


@contextmanager
def clear_registered_annotations(clear_afterwards=False):
    orig_class_specs = dict(REGISTERED_CLASS_SPECS)
    orig_function_specs = dict(REGISTERED_FUNCTION_SPECS)
    orig_handlers = dict(REGISTERED_HANDLER_BY_FUNCTION)
    try:
        REGISTERED_CLASS_SPECS.clear()
        REGISTERED_FUNCTION_SPECS.clear()
        REGISTERED_HANDLER_BY_FUNCTION.clear()
        yield
    finally:
        if clear_afterwards:
            REGISTERED_CLASS_SPECS.clear()
            REGISTERED_FUNCTION_SPECS.clear()
            REGISTERED_HANDLER_BY_FUNCTION.clear()
        REGISTERED_CLASS_SPECS.update(orig_class_specs)
        REGISTERED_FUNCTION_SPECS.update(orig_function_specs)
        REGISTERED_HANDLER_BY_FUNCTION.update(orig_handlers)


def lookup_symbol_by_name(name: str) -> Symbol:
    ret = flow().global_scope.lookup_data_symbol_by_name_this_indentation(name)
    assert ret is not None, "got None for %s" % name
    return ret


# Reset dependency graph before each test to prevent unexpected stale dependency
def make_flow_fixture(**kwargs) -> Tuple[Any, Any]:
    os.environ[PYCCOLO_DEV_MODE_ENV_VAR] = "1"
    has_keyword_arg = "cell_id" in inspect.signature(shell().run_cell).parameters

    def run_cell(code, cell_id=None, cell_pos=None, ignore_exceptions=False) -> int:
        nonlocal has_keyword_arg
        next_exec_counter = cells().next_exec_counter()
        if cell_id is None:
            cell_id = next_exec_counter
        flow().set_active_cell(cell_id)
        if cell_pos is None:
            cell_pos = cells()._position_by_cell_id.get(cell_id, None)
        if cell_pos is None:
            if isinstance(cell_id, int):
                cell_pos = cell_id
            else:
                cell_pos = next_exec_counter
        cells()._position_by_cell_id[cell_id] = cell_pos
        kwargs = {}
        if has_keyword_arg:
            kwargs["cell_id"] = cell_id
        shell().run_cell(textwrap.dedent(code), **kwargs)
        # get_ipython().run_cell_magic(
        #     flow().cell_magic_name, None, textwrap.dedent(code)
        # )
        try:
            if not ignore_exceptions and getattr(sys, "last_value", None) is not None:
                last_tb = getattr(sys, "last_traceback", None)
                if last_tb is not None:
                    if last_tb.tb_frame.f_back is None:
                        # then this was raised from non-test code (no idea why)
                        raise sys.last_value
        finally:
            sys.last_value = None
            sys.last_traceback = None
        return cell_id

    test_context = kwargs.pop("test_context", True)
    setup_stmts = [
        "import sys",
        'sys.path.append("./test")',
        "import logging",
    ] + kwargs.pop("setup_stmts", [])
    extra_fixture = kwargs.pop("extra_fixture", None)
    flow_direction = kwargs.pop("flow_direction", FlowDirection.ANY_ORDER)

    @pytest.fixture(autouse=True)
    def init_or_reset_dependency_graph():
        # IPyflowInteractiveShell.clear_instance()
        print(IPyflowInteractiveShell.instance())
        NotebookFlow.clear_instance()
        NotebookFlow.instance(
            test_context=test_context,
            flow_direction=flow_direction,
            **kwargs,
        )
        DataflowTracer.clear_instance()
        DataflowTracer.instance()
        # run all at once to prevent exec counter
        # from getting too far ahead
        run_cell("\n".join(setup_stmts))
        flow().reset_cell_counter()
        # yield to execution of the actual test
        if extra_fixture is not None:
            yield from extra_fixture()
        else:
            yield
        # ensure each test didn't give failures during ast transformation
        IPyflowInteractiveShell.instance().cleanup_tracers()
        _, exc = flow().reset_exception_counter()
        if exc is not None:
            if isinstance(exc, str):
                raise Exception(exc)
            elif isinstance(exc, Exception):
                raise exc
        IPyflowInteractiveShell.instance().reset()  # reset ipython state

    return init_or_reset_dependency_graph, run_cell
