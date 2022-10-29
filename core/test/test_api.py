# -*- coding: utf-8 -*-
import logging
from test.utils import make_flow_fixture

from ipyflow.api import lift

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _flow_fixture, run_cell_ = make_flow_fixture(trace_messages_enabled=True)
_flow_fixture, run_cell_ = make_flow_fixture(
    setup_stmts=[
        "from ipyflow.api import code, deps, lift, rdeps, rusers, timestamp, users",
        "import pyccolo as pyc",
    ]
)
run_cell = run_cell_


def test_lookup_symbol_simple():
    run_cell("x = y = 42")
    run_cell("assert lift(x).readable_name == 'x'")
    run_cell("assert lift(y).readable_name == 'y'")


def test_code_simple():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell("env = pyc.exec(lift(y).code())")
    run_cell("assert env['x'] == 0")
    run_cell("assert env['y'] == 1")
    run_cell("env = pyc.exec(code(y))")
    run_cell("assert env['x'] == 0")
    run_cell("assert env['y'] == 1")


def test_timestamp():
    run_cell("x = 0")
    run_cell("y = x + 1")
    # start at 1 b/c of setup cell
    run_cell("assert timestamp(x).cell_num == 1")
    run_cell("assert timestamp(y).cell_num == 2")


def test_deps_and_users():
    run_cell("x = 0")
    run_cell("y = x + 0")
    run_cell("assert deps(x) == []")
    run_cell("assert deps(y) == [lift(x)]")
    run_cell("assert users(x) == [lift(y)]")
    run_cell("assert users(y) == []")


def test_call_deps():
    run_cell("def f(): return 0, 1, 2, 3")
    run_cell("a, b, c, d = f()")
    for sym in "a", "b", "c", "d":
        run_cell(f"assert deps({sym}) == [lift(f)]")
        run_cell(f"assert users({sym}) == []")
    run_cell("g = lambda: (0, 1, 2, 3)")
    run_cell("w, x, y, z = g()")
    for sym in "w", "x", "y", "z":
        run_cell(f"assert deps({sym}) == [lift(g)]")
        run_cell(f"assert users({sym}) == []")


def test_rdeps_and_rusers():
    run_cell("x = 0")
    run_cell("y = x + 0")
    run_cell("z = y + 0")
    run_cell("assert rdeps(x) == []")
    run_cell("assert rdeps(y) == [lift(x)]")
    run_cell("assert set(rdeps(z)) == {lift(x), lift(y)}")
    run_cell("assert set(rusers(x)) == {lift(y), lift(z)}")
    run_cell("assert rusers(y) == [lift(z)]")
    run_cell("assert rusers(z) == []")
