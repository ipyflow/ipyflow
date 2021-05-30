# -*- coding: future_annotations -*-
import logging

from nbsafety.singletons import nbs
from test.utils import make_safety_fixture

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


def num_stmts_in_slice(cell_num: int) -> int:
    return sum(len(stmts) for stmts in nbs().compute_slice_stmts(cell_num).values())


def test_simple():
    run_cell('a = 1')
    run_cell('b = 2')
    run_cell('logging.info(a)')
    run_cell('c = a + b')
    deps = set(nbs().compute_slice(4).keys())
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
    deps = set(nbs().compute_slice(4).keys())
    assert deps == {1, 2, 3, 4}, 'got %s' % deps
    slice_size = num_stmts_in_slice(4)
    assert slice_size == 4, 'got %d' % slice_size


def test_nested_symbol_usage():
    run_cell('lst = [1, 2, 3, 4, 5]')
    run_cell('lst[1] = 3')
    run_cell('logging.info(lst[1])')
    deps = set(nbs().compute_slice(3).keys())
    assert deps == {1, 2, 3}, 'got %s' % deps
    slice_size = num_stmts_in_slice(3)
    assert slice_size == 3, 'got %d' % slice_size


def test_nested_symbol_usage_with_variable_subscript():
    for static_slicing_enabled in [True, False]:
        if not static_slicing_enabled and not nbs().mut_settings.dynamic_slicing_enabled:
            continue
        orig_enabled = nbs().mut_settings.static_slicing_enabled
        try:
            nbs().mut_settings.static_slicing_enabled = static_slicing_enabled
            run_cell('x = 1')
            run_cell('lst = [1, 2, 3, 4, 5]')
            run_cell('lst[x] = 3')
            run_cell('logging.info(lst[1])')
            deps = set(nbs().compute_slice(4).keys())
            assert deps == {1, 2, 3, 4}, 'got %s' % deps
            slice_size = num_stmts_in_slice(4)
            assert slice_size == 4, 'got %d' % slice_size
        finally:
            nbs().mut_settings.static_slicing_enabled = orig_enabled


def test_liveness_timestamps():
    orig_dynamic_enabled = nbs().mut_settings.dynamic_slicing_enabled
    orig_static_enabled = nbs().mut_settings.dynamic_slicing_enabled
    try:
        nbs().mut_settings.dynamic_slicing_enabled = False
        nbs().mut_settings.static_slicing_enabled = True
        run_cell("""
x = 0
if True:
    y = 1
else:
    y = 2
""")
        run_cell('z = 42')
        run_cell('logging.info(x + 1)')
        deps = set(nbs().compute_slice(3).keys())
        assert deps == {1, 3}, 'got %s' % deps
        slice_size = num_stmts_in_slice(3)
        assert slice_size == 2, 'got %d' % slice_size
    finally:
        nbs().mut_settings.dynamic_slicing_enabled = orig_dynamic_enabled
        nbs().mut_settings.static_slicing_enabled = orig_static_enabled


def test_list_mutations():
    run_cell('dummy = 0')
    run_cell('lst = []')
    run_cell('lst.append(1)')
    run_cell('lst.append(2)')
    run_cell('lst.append(3); lst.append(4)')
    run_cell('logging.info(lst)')
    deps = set(nbs().compute_slice(5).keys())
    assert deps == {2, 3, 4, 5}, 'got %s' % deps
    slice_size = num_stmts_in_slice(5)
    assert slice_size == 5, 'got %d' % slice_size


def test_imports():
    run_cell('import numpy as np')
    run_cell('arr = np.zeros((5,))')
    run_cell('logging.info(arr * 3)')
    deps = set(nbs().compute_slice(3).keys())
    assert deps == {1, 2, 3}, 'got %s' % deps
    slice_size = num_stmts_in_slice(3)
    assert slice_size == 3, 'got %d' % slice_size


def test_handle_stale():
    run_cell('a = 1')
    run_cell('b = 2 * a')
    run_cell('a = 2')
    run_cell('logging.info(b)')
    run_cell('logging.info(b)')
    deps = set(nbs().compute_slice(4).keys())
    assert deps == {1, 2, 4}, 'got %s' % deps
    slice_size = num_stmts_in_slice(4)
    assert slice_size == 3, 'got %d' % slice_size


def test_multiple_versions_captured():
    if not nbs().mut_settings.dynamic_slicing_enabled:
        return
    orig_enabled = nbs().mut_settings.static_slicing_enabled
    try:
        nbs().mut_settings.static_slicing_enabled = False
        run_cell('x = 0')
        run_cell('logging.info(x); y = 7')
        run_cell('x = 5')
        run_cell('logging.info(x + y)')
        deps = set(nbs().compute_slice(4).keys())
        assert deps == {1, 2, 3, 4}, 'got %s' % deps
        slice_size = num_stmts_in_slice(4)
        assert slice_size == 3, 'got %d' % slice_size
    finally:
        nbs().mut_settings.static_slicing_enabled = orig_enabled


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
    deps = set(nbs().compute_slice(4).keys())
    assert deps == {1, 2, 3, 4}, 'got %s' % deps
    slice_size = num_stmts_in_slice(4)
    assert slice_size == 4, 'got %d' % slice_size


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
    deps = set(nbs().compute_slice(4).keys())
    assert deps == {1, 2, 4}, 'got %s' % deps
    slice_size = num_stmts_in_slice(4)
    assert slice_size == 3, 'got %d' % slice_size


def test_parent_usage_includes_child_update():
    run_cell('lst = [3]')
    run_cell('lst[0] += 1')
    run_cell('lst2 = lst + [5]')
    deps = set(nbs().compute_slice(3).keys())
    assert deps == {1, 2, 3}, 'got %s' % deps
    slice_size = num_stmts_in_slice(3)
    assert slice_size == 3, 'got %d' % slice_size


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
    deps = set(nbs().compute_slice(6).keys())
    assert deps == {1, 2, 3, 6}, 'got %s' % deps
    slice_size = num_stmts_in_slice(6)
    assert slice_size == 4, 'got %d' % slice_size


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
    deps = set(nbs().compute_slice(5).keys())
    assert deps == {1, 2, 4, 5}, 'got %s' % deps
    slice_size = num_stmts_in_slice(5)
    assert slice_size == 4, 'got %d' % slice_size


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
    run_cell("logging.info(y)")
    deps = set(nbs().compute_slice(6).keys())
    assert deps == {1, 2, 3, 4, 5, 6}, 'got %s' % deps
    slice_size = num_stmts_in_slice(6)
    assert slice_size == 4, 'got %d' % slice_size
    slice_size = num_stmts_in_slice(7)
    assert slice_size == 5, 'got %d' % slice_size


def test_non_relevant_child_symbol_modified():
    run_cell('lst = [0, 1, 2]')
    run_cell('lst[0] += 1')
    run_cell('lst[0] += 1')
    run_cell('lst[1] += 1')
    run_cell('lst[0] += 1')
    run_cell('lst[0] += 1')
    run_cell('logging.info(lst[1])')
    run_cell('lst[1] = 42')
    deps = set(nbs().compute_slice(7).keys())
    assert deps == {1, 4, 7}, 'got %s' % deps
    deps = set(nbs().compute_slice(8).keys())
    assert deps == {1, 8}, 'got %s' % deps
    slice_size = num_stmts_in_slice(7)
    assert slice_size == 3, 'got %d' % slice_size


def test_dynamic_only_increment():
    if not nbs().mut_settings.dynamic_slicing_enabled:
        return
    orig_enabled = nbs().mut_settings.static_slicing_enabled
    try:
        nbs().mut_settings.static_slicing_enabled = False
        run_cell('x = 0')
        run_cell('x += 1')
        run_cell('logging.info(x)')
        deps = set(nbs().compute_slice(3).keys())
        assert deps == {1, 2, 3}, 'got %s' % deps
        slice_size = num_stmts_in_slice(3)
        assert slice_size == 3, 'got %d' % slice_size
    finally:
        nbs().mut_settings.static_slicing_enabled = orig_enabled


def test_dynamic_only_variable_subscript():
    if not nbs().mut_settings.dynamic_slicing_enabled:
        return
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
        deps = set(nbs().compute_slice(11).keys())
        assert deps == {1, 2, 5, 6, 7, 10, 11}, 'got %s' % deps
        slice_size = num_stmts_in_slice(11)
        assert slice_size == len(deps), 'got %d' % slice_size
    finally:
        nbs().mut_settings.static_slicing_enabled = orig_enabled


def test_handler():
    if not nbs().mut_settings.dynamic_slicing_enabled:
        return
    orig_enabled = nbs().mut_settings.static_slicing_enabled
    try:
        nbs().mut_settings.static_slicing_enabled = False
        run_cell("""
try:
    r = map()
except:
    success = False""")
        run_cell("""
try:
    logging.info("%d %d", (1, 2))
except:
    success = False""")
        deps = set(nbs().compute_slice(2).keys())
        assert deps == {2}, 'got %s' % deps
        slice_size = num_stmts_in_slice(2)
        assert slice_size == 1, 'got %d' % slice_size
    finally:
        nbs().mut_settings.static_slicing_enabled = orig_enabled
