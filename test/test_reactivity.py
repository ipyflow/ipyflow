# -*- coding: future_annotations -*-
import logging
from typing import TYPE_CHECKING

from nbsafety.singletons import nbs
from test.utils import make_safety_fixture

if TYPE_CHECKING:
    from typing import Set

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _safety_fixture, run_cell_ = make_safety_fixture(trace_messages_enabled=True)
_safety_fixture, run_cell_ = make_safety_fixture()


def run_cell(cell: str, cell_id=None, **kwargs) -> None:
    """Mocks the `change active cell` portion of the comm protocol"""
    if cell_id is None:
        cell_id = nbs().cell_counter()
    nbs().set_active_cell(cell_id)
    run_cell_(cell, **kwargs)


def run_reactively(cell: str) -> Set[int]:
    executed_cells = set()
    next_cell_to_run = cell
    next_cell_to_run_id = None
    while next_cell_to_run is not None:
        run_cell(next_cell_to_run, cell_id=next_cell_to_run_id)
        next_cell_to_run = None
        fresh = sorted(nbs().check_and_link_multiple_cells()['fresh_cells'])
        for fresh_cell_id in fresh:
            if fresh_cell_id not in executed_cells:
                executed_cells.add(fresh_cell_id)
                next_cell_to_run = nbs().cell_content_by_cell_id[fresh_cell_id]
                next_cell_to_run_id = fresh_cell_id
                break
    return executed_cells


def test_mutate_one_list_entry():
    run_cell('lst = [1, 2, 3]')
    run_cell('logging.info(lst[0])')
    run_cell('logging.info(lst[1])')
    run_cell('logging.info(lst[2])')
    for i in range(3):
        reexeced = run_reactively(f'lst[{i}] += 1')
        assert reexeced == {i + 2}, 'got %s' % reexeced
    reexeced = run_reactively('lst.append(3)')
    assert reexeced == set(), 'got %s' % reexeced
