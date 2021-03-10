# -*- coding: future_annotations -*-
import logging

from .utils import make_safety_fixture, skipif_known_failing

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _safety_fixture, run_cell_ = make_safety_fixture(trace_messages_enabled=True)
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


def test_non_idempotent_subscript():
    run_cell("""
class IncDict:
    def __init__(self):
        self.val = 0
    def __getitem__(self, val):
        self.val += val
        return self.val
        
d = IncDict()

d_sub_1 = d[1]
assert d_sub_1 == 1, f"got {d_sub_1}, but expected 1"
""")


def test_starred_args():
    run_cell("""
def f(foo, bar):
    return foo + bar
""")
    run_cell('args = [1, 2]')
    run_cell('f(*args)')
