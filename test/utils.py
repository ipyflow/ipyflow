# -*- coding: utf-8 -*-
import os
from typing import TYPE_CHECKING

from IPython import get_ipython
import pytest

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
    safety_state: List[Optional[NotebookSafety]] = [None]

    def run_cell(code):
        get_ipython().run_cell_magic(safety_state[0].cell_magic_name, None, code)

    store_history = kwargs.pop('store_history', False)
    setup_cells = kwargs.pop('setup_cells', [])

    @pytest.fixture(autouse=True)
    def init_or_reset_dependency_graph():
        safety_state[0] = NotebookSafety(cell_magic_name='_SAFETY_CELL_MAGIC', store_history=store_history,  **kwargs)
        run_cell('import sys')
        run_cell('sys.path.append("./test")')
        run_cell('import logging')
        for setup_cell in setup_cells:
            run_cell(setup_cell)
        yield  # yield to execution of the actual test
        get_ipython().reset()  # reset ipython state

    return init_or_reset_dependency_graph, safety_state, run_cell
