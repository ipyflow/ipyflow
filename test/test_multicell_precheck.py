# -*- coding: utf-8 -*-
import logging

from .utils import make_safety_fixture, skipif_known_failing

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
_safety_fixture, _safety_state, run_cell_ = make_safety_fixture()


def run_cell(cell):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell)


def list_to_dict(lst):
    return dict((i, val) for i, val in enumerate(lst))


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


# TODO: to get this working, we need to mock the comm protocol somehow
@skipif_known_failing
def test_refresh_after_exception_fixed():
    cells = {
        0: 'x = 0',
        1: 'y = x + 1',
        2: 'logging.info(y)',
    }
    run_cell(cells[0])
    run_cell(cells[2])
    run_cell(cells[1])
    response = _safety_state[0].multicell_precheck(cells)
    assert response['stale_output_cells'] == [2]


# TODO: to get this working, we need to mock the comm protocol somehow
@skipif_known_failing
def test_refresh_after_val_changed():
    cells = {
        0: 'x = 0',
        1: 'y = x + 1',
        2: 'logging.info(y)',
        3: 'y = 42',
    }
    run_cell(cells[0])
    run_cell(cells[1])
    run_cell(cells[2])
    run_cell(cells[3])
    response = _safety_state[0].multicell_precheck(cells)
    assert response['stale_output_cells'] == [2]
