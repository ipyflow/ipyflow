# -*- coding: future_annotations -*-
import os
import sys
from typing import TYPE_CHECKING

from IPython import get_ipython
import pytest

from nbsafety.run_mode import SafetyRunMode
from nbsafety.safety import NotebookSafety
from nbsafety.singletons import nbs

if TYPE_CHECKING:
    from typing import Any, Tuple


def should_skip_known_failing(reason='this test tests unimpled functionality'):
    return {
        'condition': os.environ.get('SHOULD_SKIP_KNOWN_FAILING', True),
        'reason': reason
    }


skipif_known_failing = pytest.mark.skipif(**should_skip_known_failing())


def assert_bool(val, msg=''):
    assert val, str(msg)


# Reset dependency graph before each test to prevent unexpected stale dependency
def make_safety_fixture(**kwargs) -> Tuple[Any, Any]:
    os.environ[SafetyRunMode.DEVELOP.value] = '1'

    def run_cell(code, ignore_exceptions=False):
        get_ipython().run_cell_magic(nbs().cell_magic_name, None, code)
        try:
            if not ignore_exceptions and getattr(sys, 'last_value', None) is not None:
                last_tb = getattr(sys, 'last_traceback', None)
                if last_tb is not None:
                    if last_tb.tb_frame.f_back is None:
                        # then this was raised from non-test code (no idea why)
                        raise sys.last_value
        finally:
            sys.last_value = None
            sys.last_traceback = None

    store_history = kwargs.pop('store_history', False)
    test_context = kwargs.pop('test_context', True)
    setup_cells = kwargs.pop('setup_cells', [])
    extra_fixture = kwargs.pop('extra_fixture', None)

    @pytest.fixture(autouse=True)
    def init_or_reset_dependency_graph():
        NotebookSafety.clear_instance()
        NotebookSafety.instance(
            cell_magic_name='_SAFETY_CELL_MAGIC',
            store_history=store_history,
            test_context=test_context,
            **kwargs
        )
        run_cell('import sys')
        run_cell('sys.path.append("./test")')
        run_cell('import logging')
        for setup_cell in setup_cells:
            run_cell(setup_cell)
        nbs().reset_cell_counter()
        # yield to execution of the actual test
        if extra_fixture is not None:
            yield from extra_fixture()
        else:
            yield
        # ensure each test didn't give failures during ast transformation
        exc = nbs().set_exception_raised_during_execution(None)
        if exc is not None:
            raise exc
        get_ipython().reset()  # reset ipython state

    return init_or_reset_dependency_graph, run_cell
