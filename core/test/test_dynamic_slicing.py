# -*- coding: utf-8 -*-
import functools
import logging
import sys
import textwrap
from test.utils import make_flow_fixture
from typing import Dict

from ipyflow.analysis.slicing import make_slice_text
from ipyflow.data_model.code_cell import cells
from ipyflow.run_mode import FlowDirection
from ipyflow.singletons import flow

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _flow_fixture, run_cell_ = make_flow_fixture(trace_messages_enabled=True)
_flow_fixture, run_cell_ = make_flow_fixture(flow_direction=FlowDirection.IN_ORDER)


def dynamic_or_static_only_test(test_f, dynamic_enabled, static_enabled):
    @functools.wraps(test_f)
    def dynamic_test_f(*args, **kwargs):
        orig_dynamic_enabled = flow().mut_settings.dynamic_slicing_enabled
        orig_static_enabled = flow().mut_settings.dynamic_slicing_enabled
        try:
            flow().mut_settings.dynamic_slicing_enabled = dynamic_enabled
            flow().mut_settings.static_slicing_enabled = static_enabled
            test_f(*args, **kwargs)
        finally:
            flow().mut_settings.dynamic_slicing_enabled = orig_dynamic_enabled
            flow().mut_settings.static_slicing_enabled = orig_static_enabled

    return dynamic_test_f


def dynamic_only_test(test_f):
    return dynamic_or_static_only_test(test_f, True, False)


def static_only_test(test_f):
    return dynamic_or_static_only_test(test_f, False, True)


def run_cell(cell, **kwargs):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell, **kwargs)


def compute_slice_stmts(cell_num):
    return cells().from_timestamp(cell_num).compute_slice_stmts()


def compute_unparsed_slice(cell_num: int) -> Dict[int, str]:
    return cells().from_timestamp(cell_num).compute_slice()


def compute_unparsed_slice_stmts(cell_num: int) -> str:
    return cells().from_timestamp(cell_num).compute_slice(stmt_level=True)


def num_stmts_in_slice(cell_num: int) -> int:
    return sum(len(stmts) for stmts in compute_slice_stmts(cell_num).values())


def test_simple():
    run_cell("a = 1")
    run_cell("b = 2")
    run_cell("logging.info(a)")
    run_cell("c = a + b")
    deps = set(compute_unparsed_slice(4).keys())
    assert deps == {1, 2, 4}, "got %s" % deps


def test_simple_function():
    run_cell("a = 1")
    run_cell("def f(): return a")
    run_cell("a = 3")
    run_cell("b = f() + 2")
    run_cell("logging.info(b)")
    deps = set(compute_unparsed_slice(5).keys())
    assert deps == {2, 3, 4, 5}, "got %s" % deps


def test_simple_list_comprehension():
    run_cell("xs = 0")
    run_cell("ys = xs ** 2")
    run_cell("xs = 1")
    run_cell("ys = [x**2 for x in [xs]]")
    deps = set(compute_unparsed_slice(4).keys())
    assert deps == {3, 4}, "got %s" % deps


def test_dynamic_symbol_usage():
    run_cell("x = 5")
    run_cell(
        """
        class Foo:
            def foo(self):
                return x
        """
    )
    run_cell(
        """
        def foo():
            return Foo()
        """
    )
    run_cell("logging.info(foo().foo())")
    deps = set(compute_unparsed_slice(4).keys())
    assert deps == {1, 2, 3, 4}, "got %s" % deps
    slice_size = num_stmts_in_slice(4)
    assert slice_size == 4, "got %d" % slice_size


def test_nested_symbol_usage():
    run_cell("lst = [1, 2, 3, 4, 5]")
    run_cell("lst[1] = 3")
    run_cell("logging.info(lst[1])")
    deps = set(compute_unparsed_slice(3).keys())
    assert deps == {1, 2, 3}, "got %s" % deps
    slice_size = num_stmts_in_slice(3)
    assert slice_size == 3, "got %d" % slice_size


def test_nested_symbol_usage_with_variable_subscript():
    for static_slicing_enabled in [True, False]:
        if (
            not static_slicing_enabled
            and not flow().mut_settings.dynamic_slicing_enabled
        ):
            continue
        orig_enabled = flow().mut_settings.static_slicing_enabled
        try:
            flow().mut_settings.static_slicing_enabled = static_slicing_enabled
            run_cell("x = 1")
            run_cell("lst = [1, 2, 3, 4, 5]")
            run_cell("lst[x] = 3")
            run_cell("logging.info(lst[1])")
            deps = set(compute_unparsed_slice(4).keys())
            assert deps == {1, 2, 3, 4}, "got %s" % deps
            slice_size = num_stmts_in_slice(4)
            assert slice_size == 4, "got %d" % slice_size
        finally:
            flow().mut_settings.static_slicing_enabled = orig_enabled


@static_only_test
def test_liveness_timestamps():
    run_cell(
        """
        x = 0
        if True:
            y = 1
        else:
            y = 2
        """
    )
    run_cell("z = 42")
    run_cell("logging.info(x + 1)")
    deps = set(compute_unparsed_slice(3).keys())
    assert deps == {1, 3}, "got %s" % deps
    slice_size = num_stmts_in_slice(3)
    assert slice_size == 2, "got %d" % slice_size


def test_list_mutations():
    run_cell("dummy = 0")
    run_cell("lst = []")
    run_cell("lst.append(1)")
    run_cell("lst.append(2)")
    run_cell("lst.append(3); lst.append(4)")
    run_cell("logging.info(lst)")
    deps = set(compute_unparsed_slice(5).keys())
    assert deps == {2, 3, 4, 5}, "got %s" % deps
    slice_size = num_stmts_in_slice(5)
    assert slice_size == 5, "got %d" % slice_size


def test_imports():
    run_cell("import numpy as np")
    run_cell("arr = np.zeros((5,))")
    run_cell("logging.info(arr * 3)")
    deps = set(compute_unparsed_slice(3).keys())
    assert deps == {1, 2, 3}, "got %s" % deps
    slice_size = num_stmts_in_slice(3)
    assert slice_size == 3, "got %d" % slice_size


def test_handle_waiters():
    run_cell("a = 1")
    run_cell("b = 2 * a")
    run_cell("a = 2")
    run_cell("logging.info(b)")
    run_cell("logging.info(b)")
    deps = set(compute_unparsed_slice(4).keys())
    assert deps == {1, 2, 4}, "got %s" % deps
    slice_size = num_stmts_in_slice(4)
    assert slice_size == 3, "got %d" % slice_size


@dynamic_only_test
def test_multiple_versions_captured():
    run_cell("x = 0")
    run_cell("logging.info(x); y = 7")
    run_cell("x = 5")
    run_cell("logging.info(x + y)")
    deps = set(compute_unparsed_slice(4).keys())
    assert deps == {1, 2, 3, 4}, "got %s" % deps
    slice_size = num_stmts_in_slice(4)
    assert slice_size == 3, "got %d" % slice_size


@dynamic_only_test
def test_tuple_unpack_used_in_funcall_before_after_update_one():
    run_cell("x, y = 0, 0", cell_id=1)
    run_cell("def get_sum(): return x + y", cell_id=2)
    run_cell("z = get_sum()", cell_id=3)
    run_cell("logging.info(z)", cell_id=4)
    deps = set(compute_unparsed_slice(4).keys())
    assert deps == {1, 2, 3, 4}, "got %s" % deps
    slice_size = num_stmts_in_slice(4)
    assert slice_size == 4, "got %d" % slice_size
    run_cell("x, y = 0, 1", cell_id=1)
    run_cell("z = get_sum()", cell_id=3)
    run_cell("logging.info(z)", cell_id=4)
    deps = set(compute_unparsed_slice(7).keys())
    assert deps == {2, 5, 6, 7}, "got %s" % deps
    slice_size = num_stmts_in_slice(7)
    assert slice_size == 4, "got %d" % slice_size
    slice_text = make_slice_text(compute_unparsed_slice_stmts(7), blacken=True).strip()
    expected = textwrap.dedent(
        """
        # Cell 2
        def get_sum():
            return x + y
            
            
        # Cell 5
        x, y = 0, 1
        
        # Cell 6
        z = get_sum()
        
        # Cell 7
        logging.info(z)
        """
    ).strip()
    assert slice_text == expected, "got %s instead of %s" % (slice_text, expected)


def test_version_used_when_live():
    run_cell("x = 0")
    run_cell(
        """
        if True:
            y = 7
        else:
            # even though this branch is not taken,
            # liveness-based usage should detect the
            # version of `x` used at the time it was
            # live, meaning cell 1 should get included
            # in the slice
            logging.info(x)
        """
    )
    run_cell("x = 5")
    run_cell("logging.info(x + y)")
    deps = set(compute_unparsed_slice(4).keys())
    assert deps == {1, 2, 3, 4}, "got %s" % deps
    slice_size = num_stmts_in_slice(4)
    assert slice_size == 4, "got %d" % slice_size


def test_no_definitely_spurious_cells():
    run_cell("x = 0")
    run_cell(
        """
        if True:
            y = 7
        else:
            # even though this branch is not taken,
            # liveness-based usage should detect the
            # version of `x` used at the time it was
            # live, meaning cell 1 should get included
            # in the slice
            logging.info(x)
        """
    )
    run_cell("x = 5")
    run_cell("logging.info(y)")
    deps = set(compute_unparsed_slice(4).keys())
    assert deps == {1, 2, 4}, "got %s" % deps
    slice_size = num_stmts_in_slice(4)
    assert slice_size == 3, "got %d" % slice_size


def test_parent_usage_includes_child_update():
    run_cell("lst = [3]")
    run_cell("lst[0] += 1")
    run_cell("lst2 = lst + [5]")
    deps = set(compute_unparsed_slice(3).keys())
    assert deps == {1, 2, 3}, "got %s" % deps
    slice_size = num_stmts_in_slice(3)
    assert slice_size == 3, "got %d" % slice_size


def test_object_subscripting():
    run_cell(
        """
        class Foo:
            def __init__(self):
                self.counter = 0
                
            def inc(self):
                self.counter += 1

            def bar(self):
                return {'baz': 0}
        """
    )
    run_cell("obj = Foo()")
    run_cell("name = 'baz'")
    run_cell("obj.bar()[name]")
    run_cell("something_i_dont_care_about = obj.bar()[name]")
    run_cell("something_i_care_about = obj.bar()[name]")
    deps = set(compute_unparsed_slice(6).keys())
    assert deps == {1, 2, 3, 6}, "got %s" % deps
    slice_size = num_stmts_in_slice(6)
    assert slice_size == 4, "got %d" % slice_size


def test_complicated_subscripting():
    run_cell(
        """
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
        """
    )
    run_cell(
        """
        class Bar:
            def __init__(self):
                self.counter = 0
            
            def foo(self, indicator):
                if indicator == 1:
                    return the_dictionary
                
                return Foo()
        """
    )
    run_cell("x = 1")
    run_cell("the_dictionary = {'something': 1}")
    run_cell("Foo().new(0).foo(1)")
    deps = set(compute_unparsed_slice(5).keys())
    assert deps == {1, 2, 4, 5}, "got %s" % deps
    slice_size = num_stmts_in_slice(5)
    assert slice_size == 4, "got %d" % slice_size


def test_complicated_subscripting_use_conditional():
    run_cell(
        """
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
        """
    )
    run_cell(
        """
        class Bar:
            def __init__(self):
                self.counter = 0
            
            def foo(self, indicator):
                if indicator == 1:
                    return the_dictionary
                
                return Foo()
        """
    )
    run_cell("x = 1")
    run_cell("the_dictionary = {'something': 1}")
    run_cell(
        """
        y = Foo().new(0).foo(1)
        z = Foo().new(1).bar(0)
        """
    )
    run_cell("logging.info(z)")
    run_cell("logging.info(y)")
    deps = set(compute_unparsed_slice(6).keys())
    assert deps == {1, 2, 3, 4, 5, 6}, "got %s" % deps
    slice_size = num_stmts_in_slice(6)
    assert slice_size == 4, "got %d" % slice_size
    slice_size = num_stmts_in_slice(7)
    assert slice_size == 5, "got %d" % slice_size


def test_non_relevant_child_symbol_modified():
    run_cell("lst = [0, 1, 2]")
    run_cell("lst[0] += 1")
    run_cell("lst[0] += 1")
    run_cell("lst[1] += 1")
    run_cell("lst[0] += 1")
    run_cell("lst[0] += 1")
    run_cell("logging.info(lst[1])")
    run_cell("lst[1] = 42")
    deps = set(compute_unparsed_slice(7).keys())
    assert deps == {1, 4, 7}, "got %s" % deps
    deps = set(compute_unparsed_slice(8).keys())
    assert deps == {1, 8}, "got %s" % deps
    slice_size = num_stmts_in_slice(7)
    assert slice_size == 3, "got %d" % slice_size


@dynamic_only_test
def test_dynamic_only_increment():
    flow().mut_settings.static_slicing_enabled = False
    run_cell("x = 0")
    run_cell("x += 1")
    run_cell("logging.info(x)")
    deps = set(compute_unparsed_slice(3).keys())
    assert deps == {1, 2, 3}, "got %s" % deps
    slice_size = num_stmts_in_slice(3)
    assert slice_size == 3, "got %d" % slice_size


@dynamic_only_test
def test_dynamic_only_variable_subscript():
    flow().mut_settings.static_slicing_enabled = False
    run_cell("lst = [0, 1, 2]")
    run_cell("x = 0")
    run_cell("lst[x] += 1")
    run_cell("lst[x] += 1")
    run_cell("x += 1")
    run_cell("lst[x] += 1")
    run_cell("x -= 1")
    run_cell("lst[x] += 1")
    run_cell("lst[x] += 1")
    run_cell("x += 1")
    run_cell("logging.info(lst[x])")
    deps = set(compute_unparsed_slice(11).keys())
    assert deps == {1, 2, 5, 6, 7, 10, 11}, "got %s" % deps
    slice_size = num_stmts_in_slice(11)
    assert slice_size == len(deps), "got %d" % slice_size


@dynamic_only_test
def test_handler():
    run_cell(
        """
        try:
            r = map()
        except:
            success = False
        """
    )
    run_cell(
        """
        try:
            logging.info("%d %d", (1, 2))
        except:
            success = False
        """
    )
    deps = set(compute_unparsed_slice(2).keys())
    assert deps == {2}, "got %s" % deps
    slice_size = num_stmts_in_slice(2)
    assert slice_size == 1, "got %d" % slice_size


@dynamic_only_test
def test_list_delete():
    run_cell("lst = [0, 1, 2, 3, 4, 5, 6]")
    run_cell("lst.append(7)")
    run_cell("del lst[0]")
    run_cell("del lst[2]")
    run_cell("logging.info(lst[1])")
    deps = set(compute_unparsed_slice(5).keys())
    assert deps == {1, 2, 3, 5}, "got %s" % deps
    slice_size = num_stmts_in_slice(5)
    assert slice_size == len(deps), "got %d" % slice_size


def test_anonymous_symbols_attached_on_fun_return_do_not_interfere():
    run_cell("y = 3")
    run_cell("def f(): return y + 5")
    run_cell("f()")
    run_cell("sink = f()")
    slice_text = make_slice_text(compute_unparsed_slice_stmts(4), blacken=True).strip()
    expected = textwrap.dedent(
        """
        # Cell 1
        y = 3
        
        # Cell 2
        def f():
            return y + 5


        # Cell 4
        sink = f()
        """
    ).strip()
    assert slice_text == expected, "got %s instead of %s" % (slice_text, expected)


def test_namespace_contributions():
    run_cell("import pandas as pd")
    run_cell('df = pd.DataFrame({"a": [0,1], "b": [2., 3.]})')
    run_cell("df['x'] = df.a + 1")
    run_cell("df['y'] = df.a + 2")
    run_cell("df['z'] = df.b + 3")
    run_cell("df.dropna()")
    deps = set(compute_unparsed_slice(6).keys())
    assert deps == {1, 2, 3, 4, 5, 6}, "got %s" % deps
    slice_size = num_stmts_in_slice(6)
    assert slice_size == len(deps), "got %d" % slice_size


if sys.version_info >= (3, 8):

    def test_slice_with_reactive_modifiers():
        run_cell("x = 0")
        run_cell("y = $x + 1")
        run_cell("logging.info($y)")
        slice_text = make_slice_text(
            compute_unparsed_slice_stmts(3), blacken=True
        ).strip()
        expected = textwrap.dedent(
            """
            # Cell 1
            x = 0
            
            # Cell 2
            y = x + 1

            # Cell 3
            logging.info(y)
            """
        ).strip()
        assert slice_text == expected, "got %s instead of %s" % (slice_text, expected)
