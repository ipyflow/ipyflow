# -*- coding: utf-8 -*-
import logging

from ipyflow.api import lift
from test.utils import make_flow_fixture

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _flow_fixture, run_cell_ = make_flow_fixture(trace_messages_enabled=True)
_flow_fixture, run_cell_ = make_flow_fixture(setup_cells=["from ipyflow.api import lift"])
run_cell = run_cell_


def test_lookup_symbol_simple():
    run_cell("x = y = 42")
    run_cell("assert lift(x).readable_name == 'x'")
    run_cell("assert lift(y).readable_name == 'y'")
