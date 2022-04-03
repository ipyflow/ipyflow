# -*- coding: utf-8 -*-
import os
import sys
import textwrap
from typing import Any, Tuple

from IPython import get_ipython
import pytest

from ipyflow.data_model.code_cell import cells
from ipyflow.kernel.kernel import DataflowKernelBase
from ipyflow.run_mode import FlowRunMode
from ipyflow.flow import NotebookFlow
from ipyflow.singletons import flow
from ipyflow.tracing.ipyflow_tracer import DataflowTracer


def should_skip_known_failing(reason="this test tests unimpled functionality"):
    return {
        "condition": os.environ.get("SHOULD_SKIP_KNOWN_FAILING", True),
        "reason": reason,
    }


skipif_known_failing = pytest.mark.skipif(**should_skip_known_failing())


def assert_bool(val, msg=""):
    assert val, str(msg)


# Reset dependency graph before each test to prevent unexpected stale dependency
def make_flow_fixture(**kwargs) -> Tuple[Any, Any]:
    os.environ[FlowRunMode.DEVELOP.value] = "1"

    def run_cell(code, cell_id=None, ignore_exceptions=False) -> int:
        if cell_id is None:
            cell_id = cells().next_exec_counter()
        flow().set_active_cell(cell_id)
        get_ipython().run_cell_magic(
            flow().cell_magic_name, None, textwrap.dedent(code)
        )
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
    setup_cells = [
        "import sys",
        'sys.path.append("./core/test")',
        "import logging",
    ] + kwargs.pop("setup_cells", [])
    extra_fixture = kwargs.pop("extra_fixture", None)

    @pytest.fixture(autouse=True)
    def init_or_reset_dependency_graph():
        DataflowKernelBase.clear_instance()
        DataflowKernelBase.instance(
            store_history=False,
        )
        NotebookFlow.clear_instance()
        NotebookFlow.instance(
            cell_magic_name="_SAFETY_CELL_MAGIC",
            test_context=test_context,
            **kwargs,
        )
        DataflowTracer.clear_instance()
        DataflowTracer.instance()
        # run all at once to prevent exec counter
        # from getting too far ahead
        run_cell("\n".join(setup_cells))
        flow().reset_cell_counter()
        # yield to execution of the actual test
        if extra_fixture is not None:
            yield from extra_fixture()
        else:
            yield
        # ensure each test didn't give failures during ast transformation
        DataflowKernelBase.instance().cleanup_tracers()
        exc = flow().set_exception_raised_during_execution(None)
        if exc is not None:
            raise exc
        get_ipython().reset()  # reset ipython state

    return init_or_reset_dependency_graph, run_cell
