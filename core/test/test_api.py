# -*- coding: utf-8 -*-
import logging
from test.utils import make_flow_fixture

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _flow_fixture, run_cell_ = make_flow_fixture(trace_messages_enabled=True)
_flow_fixture, run_cell_ = make_flow_fixture(
    setup_stmts=[
        "from ipyflow.api import code, deps, has_tag, lift, rdeps, rusers, set_tag, timestamp, users, unset_tag",
        "import pyccolo as pyc",
    ]
)
run_cell = run_cell_


def test_lookup_symbol_simple():
    run_cell("x = y = 42")
    run_cell("assert lift(x).readable_name == 'x'")
    run_cell("assert lift(y).readable_name == 'y'")


def test_code_simple():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell("env = pyc.exec(str(lift(y).code()))")
    run_cell("assert env['x'] == 0")
    run_cell("assert env['y'] == 1")
    run_cell("env = pyc.exec(str(code(y)))")
    run_cell("assert env['x'] == 0")
    run_cell("assert env['y'] == 1")


def test_timestamp():
    run_cell("x = 0")
    run_cell("y = x + 1")
    # start at 1 b/c of setup cell
    run_cell("assert timestamp(x).cell_num == 1")
    run_cell("assert timestamp(y).cell_num == 2")


def test_deps_and_users():
    run_cell("x = 0")
    run_cell("y = x + 0")
    run_cell("assert deps(x) == []")
    run_cell("assert deps(y) == [lift(x)]")
    run_cell("assert users(x) == [lift(y)]")
    run_cell("assert users(y) == []")


def test_multiple_deps_from_funcall():
    run_cell(
        """
        def f():
            x = 0
            y = 1
            return x + y
    """
    )
    run_cell("z = f()")
    run_cell("print(deps(z))")
    run_cell("print(deps(z))")
    run_cell("assert sorted([repr(d) for d in deps(z)]) == ['<f>', '<x>', '<y>']")


def test_function_dependencies():
    run_cell(
        """
        class DummyClass:
            class_static_val = ["DummyClass.class_static_val"]

            @staticmethod
            def class_static_method():
                class_static_method_val_1 = ["DummyClass.class_static_method_val_1"]
                class_static_method_val_2 = ["DummyClass.class_static_method_val_2"]
                return class_static_method_val_1 + class_static_method_val_2

            def __init__(self):
                self.class_val_1 = ["class_val_1"]
                self.class_val_2 = ["class_val_2"]
                self.class_val_3 = ["class_val_3"]

            def class_instance_method(self):
                return self.class_val_1 + self.class_val_2 + self.class_val_3
    """
    )
    run_cell(
        """
        x = []
        class_instance = DummyClass()
        x += class_instance.class_instance_method()
    """
    )
    run_cell(
        "assert sorted([repr(d) for d in deps(x)]) == ["
        "'<DummyClass.class_instance_method>', "
        "'<class_instance.class_val_1>', "
        "'<class_instance.class_val_2>', "
        "'<class_instance.class_val_3>', "
        "'<class_instance>']"
    )
    run_cell(
        """
        x = []
        x += DummyClass.class_static_method()
    """
    )
    run_cell(
        "assert sorted([repr(d) for d in deps(x)]) == ["
        "'<DummyClass.class_static_method>', "
        "'<DummyClass>', "
        "'<class_static_method_val_1>', "
        "'<class_static_method_val_2>']"
    )
    run_cell(
        """
        x = []
        x += class_instance.class_static_method()
    """
    )
    run_cell(
        "assert sorted([repr(d) for d in deps(x)]) == ["
        "'<DummyClass.class_static_method>', "
        "'<class_instance>', "
        "'<class_static_method_val_1>', "
        "'<class_static_method_val_2>']"
    )


def test_call_deps():
    run_cell("def f(): return 0, 1, 2, 3")
    run_cell("a, b, c, d = f()")
    for sym in "a", "b", "c", "d":
        run_cell(f"assert deps({sym}) == [lift(f)]")
        run_cell(f"assert users({sym}) == []")
    run_cell("g = lambda: (0, 1, 2, 3)")
    run_cell("w, x, y, z = g()")
    for sym in "w", "x", "y", "z":
        run_cell(f"assert deps({sym}) == [lift(g)]")
        run_cell(f"assert users({sym}) == []")


def test_rdeps_and_rusers():
    run_cell("x = 0")
    run_cell("y = x + 0")
    run_cell("z = y + 0")
    run_cell("assert rdeps(x) == []")
    run_cell("assert rdeps(y) == [lift(x)]")
    run_cell("assert set(rdeps(z)) == {lift(x), lift(y)}")
    run_cell("assert set(rusers(x)) == {lift(y), lift(z)}")
    run_cell("assert rusers(y) == [lift(z)]")
    run_cell("assert rusers(z) == []")


def test_tags():
    run_cell("x = y = 0")
    run_cell("assert not has_tag(x, 'foo')")
    run_cell("assert not has_tag(y, 'foo')")
    run_cell("set_tag(x, 'foo')")
    run_cell("assert has_tag(x, 'foo')")
    run_cell("assert not has_tag(y, 'foo')")
    run_cell("unset_tag(x, 'foo')")
    run_cell("assert not has_tag(x, 'foo')")
    run_cell("assert not has_tag(y, 'foo')")
    run_cell("unset_tag(y, 'foo')")
    run_cell("assert not has_tag(x, 'foo')")
    run_cell("assert not has_tag(y, 'foo')")
