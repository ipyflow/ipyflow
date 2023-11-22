# -*- coding: utf-8 -*-
import logging

from ipyflow import cells, flow, shell

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


def test_ints():
    first = cells(run_cell("x = 0", cell_id="first"))
    second = cells(run_cell("%%memoize\ny = x + 1", cell_id="second"))
    assert shell().user_ns["y"] == 1
    assert flow().global_scope["y"].obj == 1
    assert second.is_memoized
    assert not second.skipped_due_to_memoization
    run_cell("x = 0", cell_id=first.id)
    second = cells(run_cell("%%memoize\ny = x + 1", cell_id=second.id))
    assert second.is_memoized
    assert second.skipped_due_to_memoization
    assert shell().user_ns["y"] == 1
    assert flow().global_scope["y"].obj == 1
    run_cell("x = 1", cell_id=first.id)
    second = cells(run_cell("%%memoize\ny = x + 1", cell_id=second.id))
    assert shell().user_ns["y"] == 2
    assert flow().global_scope["y"].obj == 2
    assert second.is_memoized
    assert not second.skipped_due_to_memoization
    run_cell("x = 0", cell_id=first.id)
    second = cells(run_cell("%%memoize\ny = x + 1", cell_id=second.id))
    assert second.is_memoized
    assert second.skipped_due_to_memoization
    assert shell().user_ns["y"] == 1
    assert flow().global_scope["y"].obj == 1


def test_strings():
    first = cells(run_cell("x = 'hello'", cell_id="first"))
    second = cells(run_cell("%%memoize\ny = x + ' world'", cell_id="second"))
    assert shell().user_ns["y"] == "hello world"
    assert flow().global_scope["y"].obj == "hello world"
    assert second.is_memoized
    assert not second.skipped_due_to_memoization
    run_cell("x = 'hello'", cell_id=first.id)
    second = cells(run_cell("%%memoize\ny = x + ' world'", cell_id=second.id))
    assert second.is_memoized
    assert second.skipped_due_to_memoization
    run_cell("x = 'hi'", cell_id=first.id)
    second = cells(run_cell("%%memoize\ny = x + ' world'", cell_id=second.id))
    assert shell().user_ns["y"] == "hi world"
    assert flow().global_scope["y"].obj == "hi world"
    assert second.is_memoized
    assert not second.skipped_due_to_memoization
    run_cell("x = 'hello'", cell_id=first.id)
    second = cells(run_cell("%%memoize\ny = x + ' world'", cell_id=second.id))
    assert second.is_memoized
    assert second.skipped_due_to_memoization
    assert shell().user_ns["y"] == "hello world"
    assert flow().global_scope["y"].obj == "hello world"


def test_sets():
    first = cells(run_cell("x = {0}", cell_id="first"))
    second = cells(run_cell("%%memoize\ny = x | {1}", cell_id="second"))
    assert shell().user_ns["y"] == {0, 1}
    assert flow().global_scope["y"].obj == {0, 1}
    assert second.is_memoized
    assert not second.skipped_due_to_memoization
    run_cell("x = {0}", cell_id=first.id)
    second = cells(run_cell("%%memoize\ny = x | {1}", cell_id="second"))
    assert shell().user_ns["y"] == {0, 1}
    assert flow().global_scope["y"].obj == {0, 1}
    assert second.is_memoized
    assert second.skipped_due_to_memoization
    run_cell("x = {2}", cell_id=first.id)
    second = cells(run_cell("%%memoize\ny = x | {1}", cell_id="second"))
    assert shell().user_ns["y"] == {1, 2}
    assert flow().global_scope["y"].obj == {1, 2}
    assert second.is_memoized
    assert not second.skipped_due_to_memoization
    run_cell("x = {0}", cell_id=first.id)
    second = cells(run_cell("%%memoize\ny = x | {1}", cell_id="second"))
    assert shell().user_ns["y"] == {0, 1}
    assert flow().global_scope["y"].obj == {0, 1}
    assert second.is_memoized
    assert second.skipped_due_to_memoization
