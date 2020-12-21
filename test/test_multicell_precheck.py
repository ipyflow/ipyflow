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
    response = _safety_state[0].check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == [3]
    assert response['fresh_cells'] == []
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
    response = _safety_state[0].check_and_link_multiple_cells(cells)
    assert response['fresh_cells'] == [2]


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
    response = _safety_state[0].check_and_link_multiple_cells(cells)
    assert response['fresh_cells'] == [2]


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
    response = _safety_state[0].check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [2, 3]


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

    response = _safety_state[0].check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == []

    cells[4] = 'x.inc()'
    run_cell(cells[4], 4)

    response = _safety_state[0].check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [2, 3]

    cells[5] = 'foo.inc()'
    run_cell(cells[5], 5)
    response = _safety_state[0].check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [2, 3, 4]

    if force_subscript_symbol_creation:
        cells[6] = 'lst[-1]'
        run_cell(cells[6], 6)
        response = _safety_state[0].check_and_link_multiple_cells(cells)
        assert response['stale_cells'] == []
        assert response['fresh_cells'] == [2, 3, 4]

    run_cell(cells[4], 4)
    run_cell('%safety show_deps foo', 1234)
    response = _safety_state[0].check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [2, 3, 5] + ([6] if force_subscript_symbol_creation else [])


@skipif_known_failing
def test_no_freshness_for_alias_assignment_post_mutation():
    cells = {
        0: 'x = []',
        1: 'y = x',
        2: 'x.append(5)',
    }
    for idx, cell in cells.items():
        run_cell(cell, idx)
    response = _safety_state[0].check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == []


def test_fresh_after_import():
    cells = {
        0: 'x = np.random.random(10)',
        1: 'import numpy as np'
    }
    for idx, cell in cells.items():
        run_cell(cell, idx)
    response = _safety_state[0].check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [0]


def test_external_object_update_propagates_to_stale_namespace_symbols():
    cells = {
        0: 'import fakelib',
        1: 'foo = fakelib.Foo()',
        2: 'logging.info(foo.x)',
        3: 'x = 42',
        4: 'foo.x = x + 1',
        5: 'x = 43',
        6: 'foo = foo.set_x(10)',
    }
    old_skip_stale = _safety_state[0].config.get('skip_unsafe_cells', True)
    try:
        _safety_state[0].config.skip_unsafe_cells = False
        for idx, cell in cells.items():
            run_cell(cell, idx)
        response = _safety_state[0].check_and_link_multiple_cells(cells)
        assert response['stale_cells'] == []
        assert response['fresh_cells'] == [2, 4]
    finally:
        _safety_state[0].config.skip_unsafe_cells = old_skip_stale


def test_symbol_on_both_sides_of_assignment():
    cells = {
        0: 'x = 0',
        1: 'y = x + 1',
        2: 'x = 42',
    }
    for idx, cell in cells.items():
        run_cell(cell, idx)
    cells[3] = 'y += 7'
    response = _safety_state[0].check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == [3]
    assert response['fresh_cells'] == [1]
    assert list(response['refresher_links'].keys()) == [1]
