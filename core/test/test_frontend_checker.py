# -*- coding: utf-8 -*-
import logging
from contextlib import contextmanager
from dataclasses import asdict
from test.utils import make_flow_fixture, skipif_known_failing
from typing import Dict

from ipyflow.data_model.code_cell import cells
from ipyflow.flow import MutableNotebookSafetySettings, NotebookSafetySettings
from ipyflow.run_mode import ExecutionSchedule, FlowDirection
from ipyflow.singletons import flow

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _flow_fixture, run_cell_ = make_flow_fixture(trace_messages_enabled=True)
_flow_fixture, run_cell_ = make_flow_fixture()


@contextmanager
def override_settings(**kwargs):
    old_settings = flow().settings
    old_mut_settings = flow().mut_settings
    new_settings = old_settings._asdict()
    new_mut_settings = asdict(old_mut_settings)
    for k, v in kwargs.items():
        if k in new_settings:
            new_settings[k] = v
        elif k in new_mut_settings:
            new_mut_settings[k] = v
        else:
            raise ValueError("key %s not in either settings or mut_settings" % k)
    new_settings = NotebookSafetySettings(**new_settings)
    new_mut_settings = MutableNotebookSafetySettings(**new_mut_settings)
    try:
        flow().settings = new_settings
        flow().mut_settings = new_mut_settings
        yield
    finally:
        flow().settings = old_settings
        flow().mut_settings = old_mut_settings


def run_cell(cell, cell_id, **kwargs):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell, cell_id=cell_id, **kwargs)


def run_all_cells(cells: Dict[int, str], **kwargs):
    for cell_id in sorted(cells.keys()):
        run_cell(cells[cell_id], cell_id=cell_id, **kwargs)


def test_simple():
    cells = {
        0: "x = 0",
        1: "y = x + 1",
        2: "logging.info(y)",
        3: "x = 42",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == {2}
    assert response.ready_cells == {1}
    assert response.waiter_links == {2: {1}}
    assert response.ready_maker_links == {1: {2}}


def test_refresh_after_exception_fixed():
    cells = {
        0: "x = 0",
        1: "y = x + 1",
        2: "logging.info(y)",
    }
    run_cell(cells[0], 0)
    run_cell(cells[2], 2, ignore_exceptions=True)
    run_cell(cells[1], 1)
    response = flow().check_and_link_multiple_cells()
    assert response.ready_cells == {2}


def test_refresh_after_val_changed():
    cells = {
        0: "x = 0",
        1: "y = x + 1",
        2: "logging.info(y)",
        3: "y = 42",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.ready_cells == {2}


def test_inner_mutation_considered_fresh():
    cells = {
        0: "lst_0 = [0,1,2]",
        1: "lst_1 = [3,4,5]",
        2: "lst = [lst_0, lst_1]",
        3: "logging.info(lst)",
        4: "lst_0.append(42)",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == {2, 3}


# @pytest.mark.parametrize("force_subscript_symbol_creation", [True, False])
def test_update_list_elem():
    force_subscript_symbol_creation = True
    cells = {
        0: (
            """
            class Foo:
                def __init__(self):
                    self.counter = 0
                    self.dummy = 0
                    
                def inc(self):
                    self.counter += 1
            """
        ),
        1: (
            """
            lst = []
            for i in range(5):
                x = Foo()
                lst.append(x)
            """
        ),
        2: (
            """
            for foo in lst:
                foo.inc()
            """
        ),
        3: "logging.info(lst)",
    }

    run_all_cells(cells)

    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set(), "got %s" % response.ready_cells

    cells[4] = "x.inc()"
    run_cell(cells[4], 4)

    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set(), "got %s" % response.waiting_cells
    assert response.ready_cells == {2, 3}, "got %s" % response.ready_cells

    cells[5] = "foo.inc()"
    run_cell(cells[5], 5)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set(), "got %s" % response.waiting_cells
    assert response.ready_cells == {2, 3, 4}, "got %s" % response.ready_cells

    if force_subscript_symbol_creation:
        cells[6] = "lst[-1]"
        run_cell(cells[6], 6)
        response = flow().check_and_link_multiple_cells()
        assert response.waiting_cells == set()
        assert response.ready_cells == {2, 3, 4}, "got %s" % response.ready_cells

    run_cell(cells[4], 4)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set(
        [2, 3, 5] + ([6] if force_subscript_symbol_creation else [])
    )


def test_no_freshness_for_alias_assignment_post_mutation():
    cells = {
        0: "x = []",
        1: "y = x",
        2: "x.append(5)",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set()


def test_fresh_after_import():
    cells = {0: "x = np.random.random(10)", 1: "import numpy as np"}
    run_all_cells(cells, ignore_exceptions=True)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == {0}


def test_external_object_update_propagates_to_waiting_namespace_symbols():
    cells = {
        0: "import fakelib",
        1: "foo = fakelib.Foo()",
        2: "logging.info(foo.x)",
        3: "x = 42",
        4: "foo.x = x + 1",
        5: "x = 43",
        6: "foo = foo.set_x(10)",
    }
    with override_settings(mark_waiting_symbol_usages_unsafe=False):
        run_all_cells(cells)
        response = flow().check_and_link_multiple_cells()
        assert response.waiting_cells == set(), "got %s" % response.waiting_cells
        assert response.ready_cells == {2, 4}


def test_symbol_on_both_sides_of_assignment():
    cells = {
        0: "x = 0",
        1: "y = x + 1",
        2: "y += 7",
        3: "x = 42",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == {2}
    assert response.ready_cells == {1}
    assert list(response.ready_maker_links.keys()) == [1]


def test_updated_namespace_after_subscript_dep_removed():
    cells = {
        0: "x = 5",
        1: "d = {x: 5}",
        2: "logging.info(d[5])",
        3: "x = 9",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == {2}
    assert response.ready_cells == {1}
    cells[1] = "d = {5: 6}"
    run_cell(cells[1], 1)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == {2}
    run_cell(cells[2], 2)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set()
    run_cell(cells[0], 0)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set(), "got %s" % response.ready_cells


def test_equal_list_update_does_induce_fresh_cell():
    cells = {
        0: 'x = ["f"] + ["o"] * 10',
        1: 'y = x + list("bar")',
        2: "logging.info(y)",
        3: 'y = list("".join(y))',
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == {2}
    run_cell('y = ("f",)', 4)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == {2, 3}


def test_equal_list_update_does_induce_fresh_cell_LITERAL_WITH_F_IS_REUSED_ON_UBUNTU_20_04_2_PYTHON_3_8_11():
    cells = {
        0: 'x = ["f"] + ["o"] * 10',
        1: 'y = x + list("bar")',
        2: "logging.info(y)",
        3: 'y = list("".join(y))',
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == {2}
    run_cell('y = ["f"]', 4)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set(), "got %s" % response.waiting_cells
    assert response.ready_cells == {2, 3}


def test_equal_dict_update_does_induce_fresh_cell():
    cells = {
        0: 'x = {"foo": 42, "bar": 43}',
        1: 'y = dict(set(x.items()) | set({"baz": 44}.items()))',
        2: "logging.info(y)",
        3: "y = dict(y.items())",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == {2}, "got %s" % response.ready_cells
    run_cell('y = {"foo": 99}', 4)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == {2, 3}


def test_list_append():
    cells = {
        0: "lst = [0, 1]",
        1: "x = lst[1] + 1",
        2: "logging.info(x)",
        3: "lst.append(2)",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set()
    run_cell("lst[1] += 42", 4)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == {2}
    assert response.ready_cells == {1}


def test_list_extend():
    cells = {
        0: "lst = [0, 1]",
        1: "x = lst[1] + 1",
        2: "logging.info(x)",
        3: "lst.extend([2, 3, 4])",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set()
    run_cell("lst[1] += 42", 4)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == {2}
    assert response.ready_cells == {1}


def test_implicit_subscript_symbol_does_not_bump_ts():
    cells = {
        0: "lst = [] + [0, 1]",
        1: "logging.info(lst)",
        2: "logging.info(lst[0])",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set()


def test_liveness_skipped_for_simple_assignment_involving_aliases():
    cells = {
        0: "lst = [1, 2, 3]",
        1: "lst2 = lst",
        2: "lst.append(4)",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set()
    run_cell("lst = [1, 2, 3, 4]", 3)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == {1, 2}, "got %s" % response.ready_cells


def test_incorrect_object_not_used_for_argument_symbols():
    cells = {
        0: "import numpy as np",
        1: "arr = np.random.randn(100)",
        2: "def f(x): return []",
        # when obj for `np` was passed to `x`, this created issues
        3: "f(np.arange(10))",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set(), "got %s" % response.ready_cells


def test_increment_by_same_amount():
    cells = {
        0: "x = 2",
        1: "y = x + 1",
        2: "logging.info(y)",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set()
    run_cell("x = 3", 0)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == {2}
    assert response.ready_cells == {1}


def test_list_insert():
    cells = {
        0: "lst = [0, 1, 2, 4, 5, 6]",
        1: "logging.info(lst[0])",
        2: "logging.info(lst[1])",
        3: "logging.info(lst[2])",
        4: "logging.info(lst[3])",
        5: "logging.info(lst[4])",
        6: "logging.info(lst[5])",
        7: "x = lst[5] + 42",
        8: "logging.info(x)",
        9: "lst.insert(3, 3)",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == {8}, "got %s" % response.waiting_cells
    assert response.ready_cells == {4, 5, 6, 7}, "got %s" % response.ready_cells


def _test_list_delete_helper(last_cell):
    cells = {
        0: "lst = [0, 1, 2, 3, 3, 4, 5, 6]",
        1: "logging.info(lst[0])",
        2: "logging.info(lst[1])",
        3: "logging.info(lst[2])",
        4: "logging.info(lst[3])",
        5: "logging.info(lst[4])",
        6: "logging.info(lst[5])",
        7: "logging.info(lst[6])",
        8: "logging.info(lst[7])",
        9: "x = lst[6] + 42",
        10: "logging.info(x)",
        11: last_cell,
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == {10}
    # TODO: ideally we would detect that lst[3] is the same after deleting
    #  and not consider cell 4 to be fresh
    # TODO: ideally cell 8 would be considered unsafe since the last entry
    #  no longer exists
    assert response.ready_cells == {4, 5, 6, 7, 9}, "got %s" % response.ready_cells


def test_list_delete():
    _test_list_delete_helper("del lst[3]")


def test_list_pop():
    _test_list_delete_helper("lst.pop(3)")


def test_list_remove():
    _test_list_delete_helper("lst.remove(3)")


def test_list_clear():
    cells = {
        0: "lst = [0]",
        1: "logging.info(lst[0])",
        2: "logging.info(lst)",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set()
    run_cell("lst.clear()", 3)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == {2}


def test_dict_clear():
    cells = {
        0: "d = {0: 0}",
        1: "logging.info(d[0])",
        2: "logging.info(d)",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set()
    run_cell("d.clear()", 3)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == {2}


def test_adhoc_pandas_series_update():
    cells = {
        0: "import pandas as pd",
        1: "df = pd.DataFrame({1: [2, 3], 4: [5, 7]})",
        2: 'df["foo"] = [8, 9]',
        3: "df.foo.dropna(inplace=True)",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set()
    run_cell('df["foo"] = [8, 9]', 4)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == {3}


def test_unsafe_order():
    cells_to_run = {
        0: "x = 0",
        1: "y = x + 1",
    }
    with override_settings(flow_order=FlowDirection.IN_ORDER):
        run_all_cells(cells_to_run)
        assert flow().out_of_order_usage_detected_counter is None
        cells().set_cell_positions({0: 0, 1: 1})
        response = flow().check_and_link_multiple_cells()
        assert not response.waiting_cells
        assert not response.ready_cells
        assert not response.waiter_links
        assert not response.ready_maker_links
        run_cell("x = y + 1", 0)
        assert flow().out_of_order_usage_detected_counter == 2


def test_qualified_import():
    cells = {
        0: "import numpy.random",
        1: "logging.info(numpy.random)",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.waiting_cells == set()
    assert response.ready_cells == set()


def test_underscore():
    cells = {
        0: "x = 0",
        1: "x + 1",
        2: "logging.info(_)",
    }
    run_all_cells(cells)
    response = flow().check_and_link_multiple_cells()
    assert response.ready_cells == set()
    assert response.waiting_cells == set()
    run_cell("x = 1", 0)
    response = flow().check_and_link_multiple_cells()
    assert response.ready_cells == {1}
    assert response.waiting_cells == {2}
    run_cell(cells[1], 1)
    response = flow().check_and_link_multiple_cells()
    assert response.ready_cells == {2}
    assert response.waiting_cells == set()


# dag tests


def test_dag_semantics_simple():
    cells = {
        0: "x = 0",
        1: "y = x + 1",
        2: "logging.info(y)",
        3: "x = 42",
        4: "y = x + 1",
        5: "logging.info(y)",
    }
    with override_settings(exec_schedule=ExecutionSchedule.DAG_BASED):
        run_all_cells(cells)
        response = flow().check_and_link_multiple_cells()
        assert response.ready_cells == set()
        assert response.waiting_cells == set()
        run_cell(cells[0], 0)
        response = flow().check_and_link_multiple_cells()
        assert response.ready_cells == {1}
        assert response.waiting_cells == {2}


def test_dag_edge_change():
    cells = {
        0: "x = 0",
        1: "y = x + 1",
        2: "logging.info(y)",
    }
    with override_settings(exec_schedule=ExecutionSchedule.DAG_BASED):
        run_all_cells(cells)
        run_cell("z = 77", 1)
        run_cell("x = 42", 0)
        response = flow().check_and_link_multiple_cells()
        assert response.ready_cells == set()
        assert response.waiting_cells == set()
        run_cell("y = x + 2", 1)
        response = flow().check_and_link_multiple_cells()
        assert response.ready_cells == {2}
        assert response.waiting_cells == set()


def test_dag_edge_hybrid():
    cells = {
        0: "x = 0",
        1: "y = x + 1",
        2: "logging.info(y)",
        3: "x = 42",
        4: "y = x + 3",
        5: "logging.info(y)",
    }
    with override_settings(
        exec_schedule=ExecutionSchedule.HYBRID_DAG_LIVENESS_BASED,
        flow_order=FlowDirection.IN_ORDER,
    ):
        run_all_cells(cells)
        run_cell("x = 1", 0)
        response = flow().check_and_link_multiple_cells()
        assert response.ready_cells == {1}
        assert response.waiting_cells == {2}
        run_cell(cells[4], 4)
        response = flow().check_and_link_multiple_cells()
        assert response.ready_cells == {1, 5}
        assert response.waiting_cells == {2}
        run_cell(cells[1], 1)
        response = flow().check_and_link_multiple_cells()
        assert response.ready_cells == {2}, "got %s" % response.ready_cells
        assert response.waiting_cells == set()
        run_cell(cells[4], 4)
        response = flow().check_and_link_multiple_cells()
        assert response.ready_cells == {5}
        assert response.waiting_cells == set()
        run_cell(cells[3], 3)
        response = flow().check_and_link_multiple_cells()
        assert response.ready_cells == {4}
        assert response.waiting_cells == {5}
