# -*- coding: utf-8 -*-
import logging
from test.utils import make_flow_fixture

from ipyflow.api import lift

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _flow_fixture, run_cell_ = make_flow_fixture(trace_messages_enabled=True)
_flow_fixture, run_cell_ = make_flow_fixture(
    setup_stmts=[
        "from ipyflow.api import code, deps, lift, timestamp",
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


def test_deps():
    run_cell("x = 0")
    run_cell("y = x + 0")
    run_cell("assert deps(x) == []")
    run_cell("assert deps(y) == [lift(x)]")
