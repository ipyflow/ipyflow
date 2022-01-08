# -*- coding: utf-8 -*-
import logging
import sys
from typing import Optional, Set, Tuple

from nbsafety.data_model.code_cell import cells
from nbsafety.run_mode import ExecutionMode
from nbsafety.singletons import nbs
from test.utils import make_safety_fixture

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _safety_fixture, run_cell_ = make_safety_fixture(trace_messages_enabled=True)
_safety_fixture, run_cell_ = make_safety_fixture()


def run_cell(
    cell_content: str, cell_id: Optional[int] = None, fresh_are_reactive: bool = False
) -> Tuple[int, Set[int]]:
    orig_mode = nbs().mut_settings.exec_mode
    try:
        if fresh_are_reactive:
            nbs().mut_settings.exec_mode = ExecutionMode.REACTIVE
        executed_cells = set()
        reactive_cells = set()
        next_content_to_run = cell_content
        next_cell_to_run_id = cell_id
        while next_content_to_run is not None:
            executed_cells.add(
                run_cell_(next_content_to_run, cell_id=next_cell_to_run_id)
            )
            if len(executed_cells) == 1:
                cell_id = next(iter(executed_cells))
            next_content_to_run = None
            checker_result = nbs().check_and_link_multiple_cells()
            if fresh_are_reactive:
                reactive_cells |= checker_result.new_fresh_cells
            else:
                reactive_cells |= checker_result.forced_reactive_cells
            for reactive_cell_id in sorted(reactive_cells - executed_cells):
                next_content_to_run = cells().from_id(reactive_cell_id).content
                next_cell_to_run_id = reactive_cell_id
                break
        return cell_id, executed_cells
    finally:
        nbs().mut_settings.exec_mode = orig_mode
        nbs().reactivity_cleanup()


def run_reactively(
    cell_content: str, cell_id: Optional[int] = None
) -> Tuple[int, Set[int]]:
    return run_cell(cell_content, cell_id=cell_id, fresh_are_reactive=True)


def test_mutate_one_list_entry():
    assert run_reactively("lst = [1, 2, 3]")[1] == {1}
    assert run_reactively("logging.info(lst[0])")[1] == {2}
    assert run_reactively("logging.info(lst[1])")[1] == {3}
    assert run_reactively("logging.info(lst[2])")[1] == {4}
    for i in range(3):
        cell_id, cells_run = run_reactively(f"lst[{i}] += 1")
        assert cells_run - {cell_id} == {i + 2}, "got %s" % cells_run
    cell_id, cells_run = run_reactively("lst.append(3)")
    assert cells_run - {cell_id} == set(), "got %s" % cells_run


def test_simple_reactive_var_load():
    assert run_cell("x = 0")[1] == {1}
    assert run_cell("y = $x + 1")[1] == {2}
    assert run_cell("logging.info($y)")[1] == {3}
    assert run_cell("x = 42")[1] == {4, 2, 3}
    cell_id, cells_run = run_cell("y = 99")
    assert cells_run - {cell_id} == {3}


def test_simple_reactive_var_store():
    assert run_cell("x = 0")[1] == {1}
    assert run_cell("y = x + 1")[1] == {2}
    assert run_cell("logging.info(y)")[1] == {3}
    assert run_cell("$x = 42")[1] == {4, 2}
    cell_id, cells_run = run_cell("$y = 99")
    assert cells_run - {cell_id} == {3}, "got %s" % cells_run


def test_simple_blocked_reactive_var_store():
    assert run_cell("x = 0")[1] == {1}
    assert run_cell("$:y = $x + 1")[1] == {2}
    assert run_cell("logging.info($y)")[1] == {3}
    assert run_cell("x = 42")[1] == {4, 2}
    cell_id, cells_run = run_cell("z = 9001")
    assert cells_run - {cell_id} == set(), "got %s" % (cells_run - {cell_id})
    cell_id, cells_run = run_cell("y = 99")
    assert cells_run - {cell_id} == {3}


def test_simple_blocked_reactive_var_load():
    assert run_cell("x = 0")[1] == {1}
    assert run_cell("y = $:x + 1")[1] == {2}
    assert run_cell("logging.info(y)")[1] == {3}
    assert run_cell("$x = 42")[1] == {4}
    cell_id, cells_run = run_cell("$y = 99")
    assert cells_run - {cell_id} == {3}, "got %s" % cells_run


def test_reactive_function_defn():
    assert run_cell("x = 0")[1] == {1}
    assert run_cell("def f(): return x")[1] == {2}
    assert run_cell("logging.info(f())")[1] == {3}
    assert run_cell("def $f(): return x + 3")[1] == {4, 3}


def test_reactive_function_call():
    assert run_cell("x = 0")[1] == {1}
    assert run_cell("def f(): return $x")[1] == {2}
    assert run_cell("logging.info(f())")[1] == {3}
    assert run_cell("x = 42")[1] == {4, 3}


def test_reactive_store_to_global_var_from_function_call():
    assert run_cell("x = 0")[1] == {1}
    assert run_cell("def f(): global x; $x = 42")[1] == {2}
    assert run_cell("logging.info(x)")[1] == {3}
    assert run_cell("f()")[1] == {4, 3}


def test_reactive_store_to_local_var_from_function_call():
    assert run_cell("x = 0")[1] == {1}
    assert run_cell("def f(): $x = 42")[1] == {2}
    assert run_cell("logging.info(x)")[1] == {3}
    assert run_cell("f()")[1] == {4}


if sys.version_info >= (3, 8):

    def test_reactive_attr_load():
        assert (
            run_cell(
                """
            from dataclasses import dataclass
            
            @dataclass
            class Example:
                foo: str
                bar: int
            
            ex = Example('hi', 42)
            """
            )[1]
            == {1}
        )
        assert run_cell("logging.info($ex)")[1] == {2}
        assert run_cell("logging.info(ex.$foo)")[1] == {3}
        assert run_cell("logging.info(ex.$bar)")[1] == {4}
        assert run_cell("logging.info($ex.foo)")[1] == {5}
        assert run_cell("logging.info($ex.bar)")[1] == {6}
        cell_id, cells_run = run_cell('ex.foo = "hello"')
        assert cells_run - {cell_id} == {2, 3, 5, 6}
        cell_id, cells_run = run_cell("ex.bar = 9001")
        assert cells_run - {cell_id} == {2, 4, 5, 6}
        cell_id, cells_run = run_cell('ex = Example("foo", 0)')
        assert cells_run - {cell_id} == {2, 3, 4, 5, 6}

    def test_reactive_attr_store():
        assert (
            run_cell(
                """
            from dataclasses import dataclass
            
            @dataclass
            class Example:
                foo: str
                bar: int
            
            ex = Example('hi', 42)
            """
            )[1]
            == {1}
        )
        assert run_cell("logging.info(ex)")[1] == {2}
        assert run_cell("logging.info(ex.foo)")[1] == {3}
        assert run_cell("logging.info(ex.bar)")[1] == {4}
        assert run_cell("logging.info(ex.$foo)")[1] == {5}
        assert run_cell("logging.info(ex.$bar)")[1] == {6}
        cell_id, cells_run = run_cell('ex.$foo = "hello"')
        assert cells_run - {cell_id} == {2, 3, 5}, "got %s" % (cells_run - {cell_id})
        cell_id, cells_run = run_cell("ex.$bar = 9001")
        assert cells_run - {cell_id} == {2, 4, 6}
        cell_id, cells_run = run_cell('$ex.foo = "wat"')
        assert cells_run - {cell_id} == {2, 3, 4, 5, 6}, "got %s" % (
            cells_run - {cell_id}
        )
        cell_id, cells_run = run_cell('ex = Example("foo", 0)')
        assert cells_run - {cell_id} == {5, 6}
        cell_id, cells_run = run_cell('$ex = Example("foo", 0)')
        assert cells_run - {cell_id} == {2, 3, 4, 5, 6}

    def test_blocked_reactive_attr_store():
        assert (
            run_cell(
                """
            from dataclasses import dataclass
            
            @dataclass
            class Example:
                foo: str
                bar: int
            
            ex = Example('hi', 42)
            """
            )[1]
            == {1}
        )
        assert run_cell("logging.info($ex)")[1] == {2}
        assert run_cell("logging.info(ex.$foo)")[1] == {3}
        assert run_cell("logging.info(ex.$bar)")[1] == {4}
        assert run_cell("logging.info($ex.foo)")[1] == {5}
        assert run_cell("logging.info($ex.bar)")[1] == {6}
        cell_id, cells_run = run_cell('ex.$:foo = "hello"')
        assert cells_run - {cell_id} == set(), "got %s" % (cells_run - {cell_id})
        cell_id, cells_run = run_cell("ex.$:bar = 9001")
        assert cells_run - {cell_id} == set(), "got %s" % (cells_run - {cell_id})
        cell_id, cells_run = run_cell('$:ex = Example("foo", 0)')
        assert cells_run - {cell_id} == set(), "got %s" % (cells_run - {cell_id})

    def test_blocked_reactive_attr_load():
        assert (
            run_cell(
                """
            from dataclasses import dataclass
            
            @dataclass
            class Example:
                foo: str
                bar: int
            
            ex = Example('hi', 42)
            """
            )[1]
            == {1}
        )
        assert run_cell("logging.info(ex)")[1] == {2}
        assert run_cell("logging.info($:ex.foo)")[1] == {3}
        assert run_cell("logging.info($:ex.bar)")[1] == {4}
        assert run_cell("logging.info(ex.$:foo)")[1] == {5}
        assert run_cell("logging.info(ex.$:bar)")[1] == {6}
        cell_id, cells_run = run_cell('ex.$foo = "hello"')
        assert cells_run - {cell_id} == {2}, "got %s" % (cells_run - {cell_id})
        cell_id, cells_run = run_cell("ex.$bar = 9001")
        assert cells_run - {cell_id} == {2}
        cell_id, cells_run = run_cell('$:ex.foo = "wat"')
        assert cells_run - {cell_id} == set(), "got %s" % (cells_run - {cell_id})
        cell_id, cells_run = run_cell('ex = Example("foo", 0)')
        assert cells_run - {cell_id} == set()
        cell_id, cells_run = run_cell('$ex = Example("foo", 0)')
        assert cells_run - {cell_id} == {2, 5, 6}

    def test_store_after_blocked_store_reactively_executes():
        assert run_cell("x = 0")[1] == {1}
        assert run_cell("logging.info(x)")[1] == {2}
        assert run_cell("$:x = 42")[1] == {3}
        assert run_cell("$x = 42")[1] == {4, 2}

    def test_nested_reactive_references():
        assert run_cell("x = 42")[1] == {1}
        assert (
            run_cell(
                """
            def assert_nonzero(v):
                assert v != 0, "Got 0 for v!"
            """
            )[1]
            == {2}
        )
        assert run_cell("$assert_nonzero($x)")[1] == {3}
        assert (
            run_cell(
                """
            def assert_nonzero(v):
                assert v != 0, "v can't be 0"
            """
            )[1]
            == {3, 4}
        )
        rerun = run_cell("x = 43")[1]
        assert rerun == {3, 6}, "got %s" % rerun

    def test_nested_reactive_references_2():
        assert run_cell("x = 42")[1] == {1}
        assert (
            run_cell(
                """
            def assert_nonzero(v):
                assert v != 0, "Got 0 for v!"
            """
            )[1]
            == {2}
        )
        assert run_cell("assert_nonzero($x)")[1] == {3}
        assert (
            run_cell(
                """
            def $assert_nonzero(v):
                assert v != 0, "v can't be 0"
            """
            )[1]
            == {3, 4}
        )
        rerun = run_cell("x = 43")[1]
        assert rerun == {3, 6}, "got %s" % rerun
