# -*- coding: utf-8 -*-
import logging
import sys

from .utils import make_flow_fixture, skipif_known_failing

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _flow_fixture, run_cell_ = make_flow_fixture(trace_messages_enabled=True)
_flow_fixture, run_cell_ = make_flow_fixture(enable_reactive_variables=True)


def run_cell(cell, **kwargs):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell, **kwargs)


def test_stack_on_tracing_reenable():
    run_cell(
        """
        x = 42

        def fake_func(y):
            z = 9
            return z

        new_xs = [fake_func(x) for _ in range(5)]

        logging.info(new_xs[0])
        """
    )


def test_non_idempotent_subscript():
    run_cell(
        """
        class IncDict:
            def __init__(self):
                self.val = 0
            def __getitem__(self, val):
                self.val += val
                return self.val
                
        d = IncDict()

        d_sub_1 = d[1]
        assert d_sub_1 == 1, f"got {d_sub_1}, but expected 1"
        """
    )


def test_starred_args():
    run_cell(
        """
        def f(foo, bar):
            return foo + bar
        """
    )
    run_cell("args = [1, 2]")
    run_cell("f(*args)")


def test_starred_assignment():
    run_cell("x = 0")
    run_cell("y = 1")
    run_cell("z = 2")
    run_cell('lst = ["foo", "bar"]')
    # just to make sure the tracer can handle a starred expr in list literal
    run_cell("s, *t = [x + 1, y + 2, z + 3, *lst]")
    run_cell("z = 42")
    run_cell("logging.info(s)")
    run_cell("logging.info(t[0])")
    run_cell("logging.info(t[1])")
    run_cell("x = 99")
    run_cell("logging.info(s)")
    run_cell("logging.info(t[0])")


def test_slices():
    run_cell("lst = list(range(10))")
    run_cell("foo = lst[3:7]")
    run_cell("lst[1:2] = foo")


def test_partial_slices():
    run_cell('s = "Hello, world!"')
    run_cell("logging.info(str(s[:7]))")
    run_cell("logging.info(str(s[7:]))")


def test_delete():
    run_cell("lst = list(range(10))")
    run_cell("del lst[0]")
    run_cell("del lst[-1]")


def test_delitem():
    run_cell(
        """
        class Foo:
            def __init__(self):
                self.lst = []
            def __getitem__(self, i):
                return self.lst[i]
            def __setitem__(self, i, v):
                self.lst[i] = v
            def __delitem__(self, i):
                del self.lst[i]
        """
    )
    run_cell("foo = Foo()")
    run_cell("foo.lst.append(0)")
    run_cell("foo.lst.append(1)")
    run_cell("foo.lst.append(2)")
    run_cell("logging.info(foo[0])")
    run_cell("foo[1] = 2")
    run_cell("del foo[-1]")


def test_empty_return():
    run_cell(
        """
        def foo():
            return
        """
    )
    run_cell("x = foo()")


def test_fancy_slices():
    run_cell(
        """
        class Foo:
            def __init__(self, x):
                self.x = x
        """
    )
    run_cell("foo = Foo(1)")
    run_cell("import numpy as np")
    run_cell("x = np.zeros((3, 3, 3))")
    run_cell("logging.info(x[:,:,:])")
    run_cell("logging.info(x[:,...])")
    run_cell("logging.info(x[1:,...])")
    run_cell("logging.info(x[foo.x])")
    run_cell("logging.info(x[foo.x:,...])")
    run_cell("logging.info(x[foo.x:foo.x+1,:,...])")


def test_fancy_slice_assign_augassign():
    run_cell("%flow trace_messages enable")
    run_cell("import numpy as np")
    run_cell("x = np.zeros((3, 3, 3))")
    run_cell("x[:, 1 ,...] = 1.")
    run_cell("x[:, 1 ,...] /= 5.")


def test_pass():
    run_cell("if True: pass")


def test_none_key():
    run_cell("d = {}")
    run_cell("d[None] = None")
    run_cell("d.clear()")


def test_global_var():
    run_cell("x = 0")
    run_cell("def f(): global x; x = 42")
    run_cell("f()")
    run_cell("assert x == 42")


def test_syntax_error_does_not_completely_mess_up_kernel():
    # first run a cell w/ syntax error, then one w/out
    # the second cell should be fine
    try:
        run_cell("x ++= 5")
    except SyntaxError:
        pass
    run_cell("x = 5")


def test_namespace_change_to_non_container():
    run_cell("x = 3, 4")
    run_cell("x = 3")


def test_reactive_modifiers_dont_happen_inside_strings():
    run_cell(
        """
        x = '''
        y = $x + $z
        '''.strip()
        """
    )
    run_cell("assert len(x) == 11")


if sys.version_info >= (3, 8):

    def test_reactive_variable_does_not_break():
        run_cell("x = 0")
        run_cell("y = $x + 1")
        run_cell("print($y)")
