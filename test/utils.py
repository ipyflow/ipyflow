# -*- coding: utf-8 -*-
import os
import sys
from typing import TYPE_CHECKING

from IPython import get_ipython
import pytest

from nbsafety.run_mode import SafetyRunMode
from nbsafety.safety import NotebookSafety

if TYPE_CHECKING:
    from typing import Any, List, Optional, Tuple


def should_skip_known_failing(reason='this test tests unimpled functionality'):
    return {
        'condition': os.environ.get('SHOULD_SKIP_KNOWN_FAILING', True),
        'reason': reason
    }


skipif_known_failing = pytest.mark.skipif(**should_skip_known_failing())


def assert_bool(val, msg=''):
    assert val, str(msg)


# Reset dependency graph before each test to prevent unexpected stale dependency
def make_safety_fixture(**kwargs) -> 'Tuple[Any, List[Optional[NotebookSafety]], Any]':
    os.environ[SafetyRunMode.DEVELOP.value] = '1'
    safety_state: List[Optional[NotebookSafety]] = [None]

    def run_cell(code, ignore_exceptions=False):
        get_ipython().run_cell_magic(safety_state[0].cell_magic_name, None, code)
        try:
            if not ignore_exceptions and getattr(sys, 'last_value', None) is not None:
                raise sys.last_value
        finally:
            sys.last_value = None

    store_history = kwargs.pop('store_history', False)
    test_context = kwargs.pop('test_context', True)
    setup_cells = kwargs.pop('setup_cells', [])

    @pytest.fixture(autouse=True)
    def init_or_reset_dependency_graph():
        safety_state[0] = NotebookSafety(
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
        yield  # yield to execution of the actual test
        # ensure each test didn't give failures during ast transformation
        exc = safety_state[0].set_ast_transformer_raised(None)
        if exc is not None:
            raise exc
        get_ipython().reset()  # reset ipython state

    return init_or_reset_dependency_graph, safety_state, run_cell
