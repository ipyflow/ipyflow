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


def test_simple():
    run_cell('a = 1')
    run_cell('b = 2')
    run_cell('logging.info(a)')
    run_cell('c = a + b')
    deps = set(nbs().get_cell_dependencies(4).keys())
    assert deps == {1, 2, 4}, 'got %s' % deps


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


def test_list_mutations():
    run_cell('dummy = 0')
    run_cell('lst = []')
    run_cell('lst.append(1)')
    run_cell('lst.append(2)')
    run_cell('lst.append(3); lst.append(4)')
    run_cell('logging.info(lst)')
    deps = set(nbs().get_cell_dependencies(5).keys())
    assert deps == {2, 3, 4, 5}, 'got %s' % deps


def test_imports():
    run_cell('import numpy as np')
    run_cell('arr = np.zeros((5,))')
    run_cell('logging.info(arr * 3)')
    deps = set(nbs().get_cell_dependencies(3).keys())
    assert deps == {1, 2, 3}, 'got %s' % deps


def test_handle_stale():
    run_cell('a = 1')
    run_cell('b = 2 * a')
    run_cell('a = 2')
    run_cell('logging.info(b)')
    run_cell('logging.info(b)')
    deps = set(nbs().get_cell_dependencies(4).keys())
    assert deps == {1, 2, 4}, 'got %s' % deps


def test_multiple_versions_captured():
    run_cell('x = 0')
    run_cell('logging.info(x); y = 7')
    run_cell('x = 5')
    run_cell('logging.info(x + y)')
    deps = set(nbs().get_cell_dependencies(4).keys())
    assert deps == {1, 2, 3, 4}, 'got %s' % deps


def test_version_used_when_live():
    run_cell('x = 0')
    run_cell("""
if True:
    y = 7
else:
    # even though this branch is not taken,
    # liveness-based usage should detect the
    # version of `x` used at the time it was
    # live, meaning cell 1 should get included
    # in the slice
    logging.info(x)
""")
    run_cell('x = 5')
    run_cell('logging.info(x + y)')
    deps = set(nbs().get_cell_dependencies(4).keys())
    assert deps == {1, 2, 3, 4}, 'got %s' % deps


def test_no_definitely_spurious_cells():
    run_cell('x = 0')
    run_cell("""
if True:
    y = 7
else:
    # even though this branch is not taken,
    # liveness-based usage should detect the
    # version of `x` used at the time it was
    # live, meaning cell 1 should get included
    # in the slice
    logging.info(x)
""")
    run_cell('x = 5')
    run_cell('logging.info(y)')
    deps = set(nbs().get_cell_dependencies(4).keys())
    assert deps == {1, 2, 4}, 'got %s' % deps


@skipif_known_failing
def test_parent_usage_includes_child_update():
    run_cell('lst = [3]')
    run_cell('lst[0] += 1')
    run_cell('lst2 = lst + [5]')
    deps = set(nbs().get_cell_dependencies(3).keys())
    assert deps == {1, 2, 3}, 'got %s' % deps
