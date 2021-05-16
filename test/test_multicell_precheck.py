# -*- coding: future_annotations -*-
from contextlib import contextmanager
import logging
from typing import TYPE_CHECKING

from nbsafety.safety import NotebookSafetySettings
from nbsafety.singletons import nbs
from test.utils import make_safety_fixture, skipif_known_failing

if TYPE_CHECKING:
    from typing import Dict

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _safety_fixture, run_cell_ = make_safety_fixture(trace_messages_enabled=True)
_safety_fixture, run_cell_ = make_safety_fixture()


@contextmanager
def override_settings(**kwargs):
    old_settings = nbs().settings
    new_settings = old_settings._asdict()
    new_settings.update(kwargs)
    new_settings = NotebookSafetySettings(**new_settings)
    try:
        nbs().settings = new_settings
        yield
    finally:
        nbs().settings = old_settings


def run_cell(cell, cell_id=None, **kwargs):
    """Mocks the `change active cell` portion of the comm protocol"""
    if cell_id is not None:
        nbs().handle({
            'type': 'change_active_cell',
            'active_cell_id': cell_id
        })
    run_cell_(cell, **kwargs)


def run_all_cells(cells: Dict[int, str], **kwargs):
    for cell_id in sorted(cells.keys()):
        run_cell(cells[cell_id], cell_id=cell_id, **kwargs)


def test_simple():
    cells = {
        0: 'x = 0',
        1: 'y = x + 1',
        2: 'logging.info(y)',
        3: 'x = 42',
    }
    run_all_cells(cells)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == [2]
    assert response['fresh_cells'] == [1]
    assert response['stale_links'] == {2: [1]}
    assert response['refresher_links'] == {1: [2]}


def test_refresh_after_exception_fixed():
    cells = {
        0: 'x = 0',
        1: 'y = x + 1',
        2: 'logging.info(y)',
    }
    run_cell(cells[0], 0)
    run_cell(cells[2], 2, ignore_exceptions=True)
    run_cell(cells[1], 1)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['fresh_cells'] == [2]


def test_refresh_after_val_changed():
    cells = {
        0: 'x = 0',
        1: 'y = x + 1',
        2: 'logging.info(y)',
        3: 'y = 42',
    }
    run_all_cells(cells)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['fresh_cells'] == [2]


def test_inner_mutation_considered_fresh():
    cells = {
        0: 'lst_0 = [0,1,2]',
        1: 'lst_1 = [3,4,5]',
        2: 'lst = [lst_0, lst_1]',
        3: 'logging.info(lst)',
        4: 'lst_0.append(42)',
    }
    run_all_cells(cells)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [2, 3]


# @pytest.mark.parametrize("force_subscript_symbol_creation", [True, False])
def test_update_list_elem():
    force_subscript_symbol_creation = True
    cells = {
        0: """
class Foo:
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

        3: 'logging.info(lst)',
    }

    run_all_cells(cells)

    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [], 'got %s' % response['fresh_cells']

    cells[4] = 'x.inc()'
    run_cell(cells[4], 4)

    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == [], 'got %s' % response['stale_cells']
    assert response['fresh_cells'] == [2, 3], 'got %s' % response['fresh_cells']

    cells[5] = 'foo.inc()'
    run_cell(cells[5], 5)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [2, 3, 4], 'got %s' % response['fresh_cells']

    if force_subscript_symbol_creation:
        cells[6] = 'lst[-1]'
        run_cell(cells[6], 6)
        response = nbs().check_and_link_multiple_cells(cells)
        assert response['stale_cells'] == []
        assert response['fresh_cells'] == [2, 3, 4]

    run_cell(cells[4], 4)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [2, 3, 5] + ([6] if force_subscript_symbol_creation else [])


@skipif_known_failing
def test_no_freshness_for_alias_assignment_post_mutation():
    cells = {
        0: 'x = []',
        1: 'y = x',
        2: 'x.append(5)',
    }
    run_all_cells(cells)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == []


def test_fresh_after_import():
    cells = {
        0: 'x = np.random.random(10)',
        1: 'import numpy as np'
    }
    run_all_cells(cells, ignore_exceptions=True)
    response = nbs().check_and_link_multiple_cells(cells)
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
    with override_settings(mark_stale_symbol_usages_unsafe=False):
        run_all_cells(cells)
        response = nbs().check_and_link_multiple_cells(cells)
        assert response['stale_cells'] == [], 'got %s' % response['stale_cells']
        assert response['fresh_cells'] == [2, 4]


def test_symbol_on_both_sides_of_assignment():
    cells = {
        0: 'x = 0',
        1: 'y = x + 1',
        2: 'y += 7',
        3: 'x = 42',
    }
    run_all_cells(cells)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == [2]
    assert response['fresh_cells'] == [1]
    assert list(response['refresher_links'].keys()) == [1]


def test_updated_namespace_after_subscript_dep_removed():
    cells = {
        0: 'x = 5',
        1: 'd = {x: 5}',
        2: 'logging.info(d[5])',
        3: 'x = 9',
    }
    run_all_cells(cells)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == [2]
    assert response['fresh_cells'] == [1]
    cells[1] = 'd = {5: 6}'
    run_cell(cells[1], 1)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [2]
    run_cell(cells[2], 2)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == []
    run_cell(cells[0], 0)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [], 'got %s' % response['fresh_cells']


def test_equal_list_update_does_not_induce_fresh_cell():
    cells = {
        0: 'x = ["f"] + ["o"] * 10',
        1: 'y = x + list("bar")',
        2: 'logging.info(y)',
        3: 'y = list("".join(y))',
    }
    run_all_cells(cells)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == []
    run_cell('y = ["f"]', 4)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [2, 3]


def test_equal_dict_update_does_not_induce_fresh_cell():
    cells = {
        0: 'x = {"foo": 42, "bar": 43}',
        1: 'y = dict(set(x.items()) | set({"baz": 44}.items()))',
        2: 'logging.info(y)',
        3: 'y = dict(y.items())',
    }
    run_all_cells(cells)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [], 'got %s' % response['fresh_cells']
    run_cell('y = {"foo": 99}', 4)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [2, 3]


def test_list_append():
    cells = {
        0: 'lst = [0, 1]',
        1: 'x = lst[1] + 1',
        2: 'logging.info(x)',
        3: 'lst.append(2)',
    }
    run_all_cells(cells)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == []
    run_cell('lst[1] += 42', 4)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == [2]
    assert response['fresh_cells'] == [1, 3]


def test_list_extend():
    cells = {
        0: 'lst = [0, 1]',
        1: 'x = lst[1] + 1',
        2: 'logging.info(x)',
        3: 'lst.extend([2, 3, 4])',
    }
    run_all_cells(cells)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == []
    run_cell('lst[1] += 42', 4)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == [2]
    assert response['fresh_cells'] == [1, 3]


def test_implicit_subscript_symbol_does_not_bump_ts():
    cells = {
        0: 'lst = [] + [0, 1]',
        1: 'logging.info(lst)',
        2: 'logging.info(lst[0])',
    }
    run_all_cells(cells)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == []


def test_liveness_skipped_for_simple_assignment_involving_aliases():
    cells = {
        0: 'lst = [1, 2, 3]',
        1: 'lst2 = lst',
        2: 'lst.append(4)',
    }
    run_all_cells(cells)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == []
    run_cell('lst = [1, 2, 3, 4]', 3)
    response = nbs().check_and_link_multiple_cells(cells)
    assert response['stale_cells'] == []
    assert response['fresh_cells'] == [1], 'got %s' % response['fresh_cells']
