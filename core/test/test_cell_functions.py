# -*- coding: utf-8 -*-
import logging
from test.utils import make_flow_fixture

from ipyflow.config import FlowDirection

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
_flow_fixture, run_cell = make_flow_fixture(flow_direction=FlowDirection.IN_ORDER)


def test_function_from_cell():
    run_cell("a = 1")
    run_cell("b = 2")
    run_cell("from ipyflow import last_run_cell")
    run_cell("a + b")
    run_cell("func = last_run_cell().to_function()")
    run_cell("assert func.__name__ == 'func'")
    run_cell("assert func(3, 4) == 7")
    run_cell("assert func(a=30, b=40) == 70")


def test_function_from_cell_no_args():
    run_cell("a = 1")
    run_cell("b = 2")
    run_cell("from ipyflow import last_run_cell")
    run_cell("a + b")
    run_cell("func = last_run_cell().to_function()")
    run_cell("assert func.__name__ == 'func'")
    run_cell("assert func() == 3")
    run_cell("b = 42")
    run_cell("assert func() == 43")
