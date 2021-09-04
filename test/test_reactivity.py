# -*- coding: future_annotations -*-
import functools
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


EXEC_LOG = []


def run_cell(cell: str, cell_id=None, store=True, **kwargs) -> None:
    """Mocks the `change active cell` portion of the comm protocol"""
    if cell_id is None:
        cell_id = len(EXEC_LOG)
    nbs().handle({
        'type': 'change_active_cell',
        'active_cell_id': cell_id,
    })
    run_cell_(cell, **kwargs)
    if store:
        EXEC_LOG.append(cell)


def run_reactively(cell: str) -> Set[int]:
    cells_by_id = dict(enumerate(EXEC_LOG))
    executed_cells = set()
    next_cell_to_run = cell
    next_cell_to_run_id = None
    while next_cell_to_run is not None:
        run_cell(next_cell_to_run, cell_id=next_cell_to_run_id, store=False)
        next_cell_to_run = None
        fresh = sorted(nbs().check_and_link_multiple_cells(cells_by_id)['fresh_cells'])
        for fresh_cell_id in fresh:
            if fresh_cell_id not in executed_cells:
                executed_cells.add(fresh_cell_id)
                next_cell_to_run = EXEC_LOG[fresh_cell_id]
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
        assert reexeced == {i + 1}, 'got %s' % reexeced
    reexeced = run_reactively('lst.append(3)')
    assert reexeced == set(), 'got %s' % reexeced


def clear_execution_log_after_running(test_f):
    @functools.wraps(test_f)
    def wrapped_test_f():
        test_f()
        EXEC_LOG.clear()
    return wrapped_test_f


for name, obj in list(globals().items()):
    if name.startswith('test_'):
        globals()[name] = clear_execution_log_after_running(obj)
