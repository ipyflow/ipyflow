# -*- coding: utf-8 -*-
import logging

import pytest

from .utils import make_safety_fixture, skipif_known_failing

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
_safety_fixture, _safety_state, run_cell_ = make_safety_fixture()


def run_cell(cell, cell_id=None):
    """Mocks the `change active cell` portion of the comm protocol"""
    if cell_id is not None:
        _safety_state[0].handle({
            'type': 'change_active_cell',
            'active_cell_id': cell_id
        })
    run_cell_(cell)


def test_simple():
    cells = {
        0: 'x = 0',
        1: 'y = x + 1',
        2: 'x = 42',
        3: 'logging.info(y)',
    }
    run_cell(cells[0])
    run_cell(cells[1])
    run_cell(cells[2])
    response = _safety_state[0].multicell_precheck(cells)
    assert response['stale_input_cells'] == [3]
    assert response['stale_output_cells'] == []
    assert response['stale_links'] == {3: [1]}
    assert response['refresher_links'] == {1: [3]}


def test_refresh_after_exception_fixed():
    cells = {
        0: 'x = 0',
        1: 'y = x + 1',
        2: 'logging.info(y)',
    }
    run_cell(cells[0], 0)
    run_cell(cells[2], 2)
    run_cell(cells[1], 1)
    response = _safety_state[0].multicell_precheck(cells)
    assert response['stale_output_cells'] == [2]


def test_refresh_after_val_changed():
    cells = {
        0: 'x = 0',
        1: 'y = x + 1',
        2: 'logging.info(y)',
        3: 'y = 42',
    }
    run_cell(cells[0], 0)
    run_cell(cells[1], 1)
    run_cell(cells[2], 2)
    run_cell(cells[3], 3)
    response = _safety_state[0].multicell_precheck(cells)
    assert response['stale_output_cells'] == [2]


def test_inner_mutation_considered_fresh():
    cells = {
        0: 'lst_0 = [0,1,2]',
        1: 'lst_1 = [3,4,5]',
        2: 'lst = [lst_0, lst_1]',
        3: 'logging.info(lst)',
        4: 'lst_0.append(42)',
    }
    for idx, cell in cells.items():
        run_cell(cell, idx)
    response = _safety_state[0].multicell_precheck(cells)
    assert response['stale_input_cells'] == []
    assert response['stale_output_cells'] == [2, 3]


@skipif_known_failing
@pytest.mark.parametrize("force_subscript_symbol_creation", [True, False])
def test_update_list_elem(force_subscript_symbol_creation):
    cells = {
        0: """
class Foo(object):
    def __init__(self):
        self.counter = 0
        self.dummy = 0
        
    def inc(self):
        self.counter += 1""",

        1: """
lst = []
for i in range(5):
    x = Foo()
    lst.append(x)""",

        2: """
for foo in lst:
    foo.inc()""",

        3: 'print(lst)',
    }

    for idx, cell in cells.items():
        run_cell(cell, idx)

    response = _safety_state[0].multicell_precheck(cells)
    assert response['stale_input_cells'] == []
    assert response['stale_output_cells'] == []

    cells[4] = 'x.inc()'
    run_cell(cells[4], 4)

    response = _safety_state[0].multicell_precheck(cells)
    assert response['stale_input_cells'] == []
    assert response['stale_output_cells'] == [2, 3]

    cells[5] = 'foo.inc()'
    run_cell(cells[5], 5)
    response = _safety_state[0].multicell_precheck(cells)
    assert response['stale_input_cells'] == []
    assert response['stale_output_cells'] == [2, 3, 4]

    if force_subscript_symbol_creation:
        cells[6] = 'lst[-1]'
        run_cell(cells[6], 6)
        response = _safety_state[0].multicell_precheck(cells)
        assert response['stale_input_cells'] == []
        assert response['stale_output_cells'] == [2, 3, 4]

    run_cell(cells[4], 4)
    response = _safety_state[0].multicell_precheck(cells)
    assert response['stale_input_cells'] == []
    assert response['stale_output_cells'] == [2, 3, 5] + ([6] if force_subscript_symbol_creation else [])


@skipif_known_failing
def test_no_freshness_for_alias_assignment_post_mutation():
    cells = {
        '0': 'x = []',
        '1': 'y = x',
        '2': 'x.append(5)',
    }
    for idx, cell in cells.items():
        run_cell(cell, idx)
    response = _safety_state[0].multicell_precheck(cells)
    assert response['stale_input_cells'] == []
    assert response['stale_output_cells'] == []
