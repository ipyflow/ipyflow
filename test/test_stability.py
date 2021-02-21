# -*- coding: utf-8 -*-
import logging

from .utils import make_safety_fixture

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _safety_fixture, run_cell_ = make_safety_fixture(setup_cells=['%safety trace_messages enable'])
_safety_fixture, run_cell_ = make_safety_fixture()


def run_cell(cell, **kwargs):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell, **kwargs)


def test_stack_on_tracing_reenable():
    run_cell("""
x = 42

def fake_func(y):
    z = 9
    return z

new_xs = [fake_func(x) for _ in range(5)]

print(new_xs[0])
""")
