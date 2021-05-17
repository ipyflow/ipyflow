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


def test_nested_symbol_usage():
    run_cell('lst = [1, 2, 3, 4, 5]')
    run_cell('lst[1] = 3')
    run_cell('logging.info(lst[1])')
    deps = set(nbs().get_cell_dependencies(3).keys())
    assert deps == {1, 2, 3}, 'got %s' % deps


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


def test_parent_usage_includes_child_update():
    run_cell('lst = [3]')
    run_cell('lst[0] += 1')
    run_cell('lst2 = lst + [5]')
    deps = set(nbs().get_cell_dependencies(3).keys())
    assert deps == {1, 2, 3}, 'got %s' % deps


def test_object_subscripting():
    run_cell("""
class Foo:
    def __init__(self):
        self.counter = 0
        
    def inc(self):
        self.counter += 1

    def bar(self):
        return {'baz': 0}
""")
    run_cell("obj = Foo()")
    run_cell("name = 'baz'")
    run_cell("obj.bar()[name]")
    run_cell("something_i_dont_care_about = obj.bar()[name]")
    run_cell("something_i_care_about = obj.bar()[name]")
    deps = set(nbs().get_cell_dependencies(6).keys())
    assert deps == {1, 2, 3, 6}, 'got %s' % deps


def test_complicated_subscripting():
    run_cell("""
class Foo:
    def __init__(self):
        self.counter = 0
        
    def inc(self):
        self.counter += 1

    def bar(self, indicator):
        if indicator == 1:
            return the_dictionary
        else:
            return x + 5
    
    def new(self, indicator):
        if indicator == 1:
            return Foo()
        
        return Bar()
""")
    run_cell("""
class Bar:
    def __init__(self):
        self.counter = 0
    
    def foo(self, indicator):
        if indicator == 1:
            return the_dictionary
        
        return Foo()
""")
    run_cell("x = 1")
    run_cell("the_dictionary = {'something': 1}")
    run_cell("Foo().new(0).foo(1)")
    deps = set(nbs().get_cell_dependencies(5).keys())
    assert deps == {1, 2, 4, 5}, 'got %s' % deps


def test_complicated_subscripting_use_conditional():
    run_cell("""
class Foo:
    def __init__(self):
        self.counter = 0
        
    def inc(self):
        self.counter += 1

    def bar(self, indicator):
        if indicator == 1:
            return the_dictionary
        else:
            return x + 5
    
    def new(self, indicator):
        if indicator == 1:
            return Foo()
        
        return Bar()
""")
    run_cell("""
class Bar:
    def __init__(self):
        self.counter = 0
    
    def foo(self, indicator):
        if indicator == 1:
            return the_dictionary
        
        return Foo()
""")
    run_cell("x = 1")
    run_cell("the_dictionary = {'something': 1}")
    run_cell("""
y = Foo().new(0).foo(1)
z = Foo().new(1).bar(0)
""")
    run_cell("logging.info(z)")
    deps = set(nbs().get_cell_dependencies(6).keys())
    assert deps == {1, 2, 3, 4, 5, 6}, 'got %s' % deps


def test_non_relevant_child_symbol_modified():
    run_cell('lst = [0, 1, 2]')
    run_cell('lst[0] += 1')
    run_cell('lst[0] += 1')
    run_cell('lst[1] += 1')
    run_cell('lst[0] += 1')
    run_cell('lst[0] += 1')
    run_cell('logging.info(lst[1])')
    deps = set(nbs().get_cell_dependencies(7).keys())
    assert deps == {1, 4, 7}, 'got %s' % deps


def test_dynamic_only_increment():
    orig_enabled = nbs().mut_settings.static_slicing_enabled
    try:
        nbs().mut_settings.static_slicing_enabled = False
        run_cell('x = 0')
        run_cell('x += 1')
        run_cell('logging.info(x)')
        deps = set(nbs().get_cell_dependencies(3).keys())
        assert deps == {1, 2, 3}, 'got %s' % deps
    finally:
        nbs().mut_settings.static_slicing_enabled = orig_enabled


def test_dynamic_only_variable_subscript():
    orig_enabled = nbs().mut_settings.static_slicing_enabled
    try:
        nbs().mut_settings.static_slicing_enabled = False
        run_cell('lst = [0, 1, 2]')
        run_cell('x = 0')
        run_cell('lst[x] += 1')
        run_cell('lst[x] += 1')
        run_cell('x += 1')
        run_cell('lst[x] += 1')
        run_cell('x -= 1')
        run_cell('lst[x] += 1')
        run_cell('lst[x] += 1')
        run_cell('x += 1')
        run_cell('logging.info(lst[x])')
        deps = set(nbs().get_cell_dependencies(11).keys())
        assert deps == {1, 2, 5, 6, 7, 10, 11}, 'got %s' % deps
    finally:
        nbs().mut_settings.static_slicing_enabled = orig_enabled


def test_handler():
    orig_enabled = nbs().mut_settings.static_slicing_enabled
    try:
        nbs().mut_settings.static_slicing_enabled = False
        run_cell("""
    try:
        r = map()
    except:
        success = False
    """)
        run_cell("""
    try:
        logging.info("%d %d", (1, 2))
    except:
        success = False
    """)
        deps = set(nbs().get_cell_dependencies(2).keys())
        assert deps == {2}, 'got %s' % deps
    finally:
        nbs().mut_settings.static_slicing_enabled = orig_enabled
