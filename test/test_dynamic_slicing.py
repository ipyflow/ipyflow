# -*- coding: future_annotations -*-
import logging

from nbsafety.singletons import nbs
from test.utils import make_safety_fixture, skipif_known_failing

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _safety_fixture, run_cell_ = make_safety_fixture(trace_messages_enabled=True)
_safety_fixture, run_cell_ = make_safety_fixture()


def run_cell(cell):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell)


def test_dynamic_symbol_usage():
    run_cell('x = 5')
    run_cell("""
class Foo:
    def foo(self):
        return x
""")
    run_cell("""
def foo():
    return Foo()
""")
    run_cell('logging.info(foo().foo())')
    deps = set(nbs().get_cell_dependencies(4).keys())
    assert deps == {1, 2, 3, 4}, 'got %s' % deps


@skipif_known_failing
def test_nested_symbol_usage():
    run_cell('lst = [1, 2, 3, 4, 5]')
    run_cell('lst[1] = 3')
    run_cell('logging.info(lst[1])')
    deps = set(nbs().get_cell_dependencies(3).keys())
    assert deps == {1, 2, 3}, 'got %s' % deps


@skipif_known_failing
def test_nested_symbol_usage_with_variable_subscript():
    run_cell('x = 1')
    run_cell('lst = [1, 2, 3, 4, 5]')
    run_cell('lst[x] = 3')
    run_cell('logging.info(lst[1])')
    deps = set(nbs().get_cell_dependencies(4).keys())
    assert deps == {1, 2, 3, 4}, 'got %s' % deps
