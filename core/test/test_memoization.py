# -*- coding: utf-8 -*-
import logging

from ipyflow.data_model.cell import cells

from .utils import make_flow_fixture, skipif_known_failing

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _flow_fixture, run_cell_ = make_flow_fixture(trace_messages_enabled=True)
_flow_fixture, run_cell_ = make_flow_fixture()


def run_cell(cell, **kwargs) -> int:
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    return run_cell_(cell, **kwargs)


def test_simple():
    first = cells(run_cell("x = 0", cell_id="first"))
    second = cells(run_cell("%%memoize\ny = x + 1", cell_id="second"))
    assert second.is_memoized
    assert not second.skipped_due_to_memoization
    run_cell("x = 0", cell_id=first.id)
    second = cells(run_cell("%%memoize\ny = x + 1", cell_id=second.id))
    assert second.is_memoized
    assert second.skipped_due_to_memoization
    run_cell("x = 1", cell_id=first.id)
    second = cells(run_cell("%%memoize\ny = x + 1", cell_id=second.id))
    assert second.is_memoized
    assert not second.skipped_due_to_memoization
