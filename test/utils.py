# -*- coding: utf-8 -*-
import os
from typing import TYPE_CHECKING

from IPython import get_ipython
import pytest

from nbsafety.safety import DependencySafety

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
def make_safety_fixture(**kwargs) -> 'Tuple[Any, List[Optional[DependencySafety]], Any]':
    safety_state: List[Optional[DependencySafety]] = [None]

    def run_cell(code):
        get_ipython().run_cell_magic(safety_state[0].cell_magic_name, None, code)

    @pytest.fixture(autouse=True)
    def init_or_reset_dependency_graph():
        safety_state[0] = DependencySafety(**kwargs)
        run_cell('import logging')
        yield  # yield to execution of the actual test
        get_ipython().reset()  # reset ipython state

    return init_or_reset_dependency_graph, safety_state, run_cell
