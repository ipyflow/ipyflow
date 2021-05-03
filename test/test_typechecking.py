# -*- coding: future_annotations -*-
import logging

from nbsafety.singletons import nbs
from test.utils import make_safety_fixture, skipif_known_failing

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _safety_fixture, run_cell_ = make_safety_fixture(trace_messages_enabled=True)
_safety_fixture, run_cell_ = make_safety_fixture(mark_typecheck_failures_unsafe=True)


def run_cell(cell, cell_id=None, **kwargs):
    """Mocks the `change active cell` portion of the comm protocol"""
    if cell_id is not None:
        nbs().handle({
            'type': 'change_active_cell',
            'active_cell_id': cell_id
        })
    run_cell_(cell, **kwargs)


def test_int_change_to_str_triggers_typecheck():
    run_cell('a = 1', 1)
    assert not nbs().get_cell_ids_needing_typecheck()
    run_cell('b = 2', 2)
    assert not nbs().get_cell_ids_needing_typecheck()
    run_cell('logging.info(a + b)', 3)
    assert not nbs().get_cell_ids_needing_typecheck()
    run_cell('b = "b"', 4)
    assert nbs().get_cell_ids_needing_typecheck() == {3}
