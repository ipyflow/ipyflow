# -*- coding: utf-8 -*-
import logging
import sys

from pyccolo.extra_builtins import EMIT_EVENT

from ipyflow.singletons import flow

from .utils import assert_bool, make_flow_fixture, skipif_known_failing

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _flow_fixture, run_cell_ = make_flow_fixture(trace_messages_enabled=True)
_flow_fixture, run_cell_ = make_flow_fixture()


def run_cell(cell, **kwargs):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell, **kwargs)


def waiter_detected():
    return flow().test_and_clear_waiter_usage_detected()


def assert_detected(msg=""):
    assert_bool(waiter_detected(), msg=msg)


def assert_false_positive(msg=""):
    """
    Same as `assert_detected` but asserts a false positive.
    Helps with searchability of false positives in case we want to fix these later.
    """
    return assert_detected(msg=msg)


def assert_not_detected(msg=""):
    assert_bool(not waiter_detected(), msg=msg)


def assert_false_negative(msg=""):
    """
    Same as `assert_not_detected` but asserts a false negative.
    Helps with searchability of false negatives in case we want to fix these later.
    """
    return assert_not_detected(msg=msg)


def test_simplest():
    run_cell("a = 1")
    run_cell("b = a + 1")
    run_cell("a = 3")
    run_cell("logging.info(b)")
    assert_detected("should have detected b has stale dep on old a")


def test_readme_example():
    run_cell("def eval_model_1(): return 0.5")
    run_cell("def eval_model_2(): return 0.85")
    run_cell('models = {"model_1": eval_model_1, "model_2": eval_model_2}')
    output = """
        best_acc, best_model = max((f(), name) for name, f in models.items())
        logging.info(f'The best model was {best_model} with an accuracy of {best_acc}.')
        """
    run_cell(output)
    run_cell("def eval_model_1(): return 0.9")
    run_cell(output)
    assert_detected("`models` depends on stale `eval_model_1`")


# TODO: to get this working properly, post-call argument
#  symbols need to have dependencies on pre-call argument
#  symbols. The place to do this is in `DataSymbol.create_symbols_for_call_args(...)`
def test_passed_sym_captured_as_dep_for_mutated_obj():
    run_cell(
        """
        class Foo:
            def __init__(self, x):
                self.x = x

        def mutate(foo, x):
            foo.x = x
        """
    )
    run_cell("foo = Foo(5)")
    run_cell("y = 7")
    run_cell("mutate(foo, y)")
    run_cell("logging.info(foo.x)")
    assert_not_detected()
    run_cell("y = 42")
    run_cell("logging.info(foo.x)")
    assert_detected("`foo.x` depends on old value of `y`")


def test_subscript_dependency():
    run_cell("lst = [0, 1, 2]")
    run_cell("x = 5")
    run_cell("y = x + lst[0]")
    run_cell("lst[0] = 10")
    run_cell("logging.info(y)")
    assert_detected("`y` depends on stale `lst[0]`")


def test_long_chain():
    run_cell("a = 1")
    run_cell("b = a + 1")
    run_cell("c = b + 1")
    run_cell("d = c + 1")
    run_cell("e = d + 1")
    run_cell("f = e + 1")
    assert_not_detected("everything OK so far")
    run_cell("a = 2")
    run_cell("logging.info(f)")
    assert_detected("f has stale dependency on old value of a")


def test_for_loop_liveness_check():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell("x = 42")
    run_cell(
        """
for i in range(2):
    logging.info(y)
    y = 7
"""
    )
    assert_detected("first use of `y` in loop has stale dependency on old value of `x`")


def test_redef_after_stale_use():
    run_cell("a = 1")
    run_cell("b = a + 1")
    run_cell("a = 3")
    run_cell(
        """
        logging.info(b)
        b = 7
        """
    )
    assert_detected("b has stale dependency on old value of a")


def test_class_redef():
    classdef = """
        class Foo:
            def __init__(self, x):
                self.x = x
        """
    run_cell(classdef)
    run_cell("x = 5")
    run_cell("foo = Foo(x)")
    run_cell(classdef)
    run_cell("logging.info(x)")
    assert_not_detected("`x` independent of class `Foo`")


def test_subscript_dependency_fp():
    run_cell("lst = [0, 1, 2]")
    run_cell("x = 5")
    run_cell("y = x + lst[0]")
    run_cell("lst[1] = 10")
    run_cell("logging.info(y)")
    assert_not_detected("y depends only on unchanged lst[0] and not on changed lst[1]")


def test_comprehension_generator_vars_not_live():
    run_cell("x = 0")
    run_cell("y = x + 6")
    run_cell("x = 42")
    run_cell("lst = [y for y in range(1) for j in range(1)]")
    assert_not_detected("`y` is not live in the list comprehension")


def test_lambda_params_not_live():
    run_cell("x = 0")
    run_cell("y = x + 6")
    run_cell("x = 42")
    run_cell("f = lambda y: y * 2")
    assert_not_detected("`y` is not live in the lambda expr")


def test_lambda_arg():
    run_cell("x = 5")
    run_cell("y = 7")
    run_cell("lam = lambda x: x * 2")
    run_cell("z = lam(y + 3)")
    run_cell("x = 42")
    run_cell("logging.info(z)")
    assert_not_detected("`z` independent of updated `x`")
    run_cell("y = 43")
    run_cell("logging.info(z)")
    assert_detected("`z` depends on old value of `y`")


def test_lambda_scope():
    run_cell(
        """
        def foo(x):
            return lambda: x + 5
        """
    )
    run_cell("x = 5")
    run_cell("y = 7")
    run_cell("lam = foo(y)")
    run_cell("z = lam()")
    run_cell("x = 42")
    run_cell("logging.info(z)")
    assert_not_detected("`z` independent of updated `x`")
    run_cell("y = 43")
    run_cell("logging.info(z)")


def test_lambda_wrapping_call():
    run_cell("z = 42")
    run_cell(
        """
        def f():
            return z
        """
    )
    run_cell("lam = lambda: f()")
    run_cell("x = lam()")
    run_cell("z = 43")
    run_cell("logging.info(x)")
    assert_detected("`x` depends on old value of `z`")


def test_lambda_with_kwarg_scope():
    run_cell("w = 99")
    run_cell(
        """
        def foo(x):
            lam = lambda t=w: t + x + 5
            return lam
        """
    )
    run_cell("x = 5")
    run_cell("y = 7")
    run_cell("lam = foo(y)")
    run_cell("z = lam()")
    run_cell("x = 42")
    run_cell("logging.info(z)")
    assert_not_detected("`z` independent of updated `x`")
    run_cell("w = 43")
    run_cell("logging.info(z)")
    assert_detected("`z` depends on old value of `y`")


def test_fundef_params_not_live():
    run_cell("x = 0")
    run_cell("y = x + 6")
    run_cell("x = 42")
    run_cell(
        """
        def f(y):
            return y + 9
        """
    )
    assert_not_detected("`y` is not live in the function")


def test_no_edge_for_comprehension_variable():
    run_cell("i = 0")
    run_cell("x = [i for i in range(10)]")
    run_cell("i = 1")
    run_cell("logging.info(x)")
    assert_not_detected("`x` does not depend on `i`")


# simple test about the basic assignment
def test_basic_assignment():
    run_cell("a = 1")
    run_cell("b = 2")
    run_cell("c = a+b")
    run_cell("d = c+1")
    run_cell('logging.info("%s, %s, %s, %s", a, b, c, d)')
    # redefine a here but not c and d
    run_cell("a = 7")
    run_cell('logging.info("%s, %s, %s, %s", a, b, c, d)')
    assert_detected("Did not detect that c's reference was changed")

    run_cell("c = a+b")
    run_cell('logging.info("%s, %s, %s, %s", a, b, c, d)')
    assert_detected("Did not detect that d's reference was changed")

    run_cell("d = c+1")
    run_cell('logging.info("%s, %s, %s, %s", a, b, c, d)')
    assert_not_detected("There should be no more dependency issue")


def test_empty_list_assignment():
    run_cell("a = [5]")
    run_cell("b = a + [6]")
    run_cell("logging.info(b)")
    run_cell("a = [6]")
    run_cell("logging.info(b)")
    assert_detected("`b` depends on stale `a`")
    run_cell("b = a + [7]")
    run_cell("a = []")
    run_cell("logging.info(b)")
    assert_detected("`b` depends on stale `a`")


# redefined function example from the project prompt
def test_redefined_function_in_list():
    run_cell(
        """
        def foo():
            return 5

        def bar():
            return 7
        """
    )
    run_cell(
        """
        funcs_to_run = [foo, bar]
        """
    )
    run_cell(
        """
        accum = 0
        for f in funcs_to_run:
            accum += f()
        logging.info(accum)
        """
    )

    # redefine foo here but not funcs_to_run
    run_cell(
        """
        def foo():
            return 10

        def bar():
            return 7
        """
    )
    run_cell(
        """
        accum = 0
        for f in funcs_to_run:
            accum += f()
        logging.info(accum)
        """
    )
    assert_detected("Did not detect that funcs_to_run's reference was changed")

    run_cell(
        """
        funcs_to_run = [foo, bar]
        """
    )
    run_cell(
        """
        accum = 0
        for f in funcs_to_run:
            accum += f()
        logging.info(accum)
        """
    )
    assert_not_detected("There should be no more dependency issue")


# like before but the function is called in a list comprehension
def test_redefined_function_for_funcall_in_list_comp():
    run_cell(
        """
        def foo():
            return 5

        def bar():
            return 7
        """
    )
    run_cell("retvals = [foo(), bar()]")
    run_cell(
        """
        accum = 0
        for ret in retvals:
            accum += ret
        logging.info(accum)
        """
    )

    # redefine foo here but not funcs_to_run
    run_cell(
        """
        def foo():
            return 10

        def bar():
            return 7
        """
    )
    run_cell("logging.info(accum)")
    assert_detected("Did not detect stale dependency of `accum` on `foo` and `bar`")


# like before but we run the list through a function before iterating
def test_redefined_function_for_funcall_in_modified_list_comp():
    run_cell(
        """
        def foo():
            return 5

        def bar():
            return 7
        """
    )
    run_cell("retvals = tuple([foo(), bar()])")
    run_cell(
        """
        accum = 0
        # for ret in map(lambda x: x * 5, retvals):
        for ret in retvals:
            accum += ret
        logging.info(accum)
        """
    )

    # redefine foo here but not funcs_to_run
    run_cell(
        """
        def foo():
            return 10

        def bar():
            return 7
        """
    )
    run_cell("logging.info(accum)")
    assert_detected("Did not detect stale dependency of `accum` on `foo` and `bar`")


def test_for_loop_with_map():
    run_cell(
        """
        accum = 0
        foo = [1, 2, 3, 4, 5]
        for ret in map(lambda x: x * 5, foo):
            accum += ret
        """
    )
    run_cell("logging.info(accum)")
    assert_not_detected("no stale dep foo -> accum")
    run_cell("foo = [0]")
    run_cell("logging.info(accum)")
    assert_detected(
        "should detect stale dep foo -> accum unless only propagating past cell bounds"
    )


def test_redefined_function_over_list_comp():
    run_cell(
        """
        def foo():
            return 5

        def bar():
            return 7

        def baz(lst):
            return map(lambda x: 3*x, lst)
        """
    )
    run_cell("retvals = baz([foo(), bar()])")
    run_cell(
        """
        accum = 0
        for ret in map(lambda x: x * 5, retvals):
            accum += ret
        """
    )
    run_cell(
        """
        def baz(lst):
            return map(lambda x: 7*x, lst)
        """
    )
    run_cell("logging.info(accum)")
    assert_detected("Did not detect stale dependency of `accum` on `baz`")


# like before but the function is called in a tuple comprehension
def test_redefined_function_for_funcall_in_tuple_comp():
    run_cell(
        """
        def foo():
            return 5

        def bar():
            return 7
        """
    )
    run_cell("retvals = (foo(), bar())")
    run_cell(
        """
        accum = 0
        for ret in retvals:
            accum += ret
        logging.info(accum)
        """
    )

    # redefine foo here but not funcs_to_run
    run_cell(
        """
        def foo():
            return 10

        def bar():
            return 7
        """
    )
    run_cell("logging.info(accum)")
    assert_detected("Did not detect stale dependency of `accum` on `foo` and `bar`")


def test_symbol_callpoint():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell(
        """
def f():
    return y + 7
"""
    )
    run_cell("z = f()")
    assert_not_detected()
    run_cell("x = 42")
    run_cell("z = f()")
    assert_detected("`y` as referenced in call of function `f` is stale")


def test_symbol_callpoint_2():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell(
        """
        def f():
            y = 9
            return y + 7
        """
    )
    run_cell("z = f()")
    assert_not_detected()
    run_cell("x = 42")
    run_cell("z = f()")
    assert_not_detected()


def test_symbol_callpoint_3():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell(
        """
        def f():
            return y + 7
        """
    )
    run_cell("z = f()")
    assert_not_detected()
    run_cell("x = 42")
    run_cell(
        """
        def f():
            return y + 7
        """
    )
    run_cell("z = f()")
    assert_detected("`y` as referenced in call of function `f` is stale")


def test_symbol_callpoint_4():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell(
        """
        def f(y):
            return y + 7
        """
    )
    run_cell("z = f(5)")
    assert_not_detected()
    run_cell("x = 42")
    run_cell("z = f(5)")
    assert_not_detected(
        "`y` as referenced in call of function `f` is an arg and therefore not stale"
    )


def test_function_arg_independent_of_outer_staleness():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell(
        """
        def f(y):
            return y + 7
        """
    )
    run_cell("z = f(5)")
    assert_not_detected()
    run_cell("x = 42")
    run_cell("logging.info(z)")
    assert_not_detected("`z` independent of `x`")


def test_lambda_capture():
    run_cell("x = 42")
    run_cell("lst = []")
    run_cell("lst.append(lambda elt: elt + x)")
    run_cell("y = lst[0](7)")
    run_cell("x = 43")
    run_cell("logging.info(y)")
    assert_detected("`y` depends on stale `x`")


# Tests about variables that have same name but in different scope.
# There shouldn't be any extra dependency because of the name.
def test_variable_scope():
    run_cell(
        """
        def func():
            x = 6
        """
    )
    run_cell("x = 7")
    run_cell("y = x")
    run_cell("z = func")
    run_cell('logging.info("%s, %s", y, z())')

    # change x inside of the function, but not x outside of the function
    run_cell("def func():\n    x = 10")
    run_cell('logging.info("%s, %s", y, z())')
    assert_detected("Did not detect the dependency change in the function")

    run_cell("y = x")
    run_cell('logging.info("%s, %s", y, z())')
    assert_detected(
        "Updating y should not solve the dependency change inside of function func"
    )

    run_cell("z = func")
    run_cell('logging.info("%s, %s", y, z())')
    assert_not_detected("Updating z should solve the problem")


def test_variable_scope_2():
    run_cell("def func():\n    x = 6")
    run_cell("x = 7")
    run_cell("y = x")
    run_cell("z = func")
    run_cell('logging.info("%s, %s", y, z())')

    # change x outside of the function, but not inside of the function
    run_cell("x = 10")
    run_cell('logging.info("%s, %s", y, z())')
    assert_detected("Did not detect the dependency change outside of the function")

    run_cell("z = func")
    run_cell('logging.info("%s, %s", y, z())')
    assert_detected(
        "Updating z should not solve the dependency change outside of function"
    )

    run_cell("y = x")
    run_cell('logging.info("%s, %s", y, z())')
    assert_not_detected("Updating y should solve the problem")


def test_default_args():
    run_cell(
        """
        x = 7
        def foo(y=x):
            return y + 5
        """
    )
    run_cell("a = foo()")
    assert_not_detected()
    run_cell("x = 10")
    assert_not_detected()
    run_cell("b = foo()")
    assert_detected("Should have detected stale dependency of fn foo() on x")


def test_same_pointer():
    # a and b are actually pointing to the same thing
    run_cell("a = [7]")
    run_cell("b = a")
    run_cell("c = b + [5]")

    run_cell("a[0] = 8")
    run_cell("logging.info(b)")
    assert_not_detected(
        "`b` is an alias of `a`, updating a should automatically update `b` as well"
    )
    run_cell("logging.info(c)")
    assert_detected(
        "`c` does not point to the same thing as `a` or `b`, thus there is a stale dependency here"
    )


def test_func_assign_objs():
    run_cell(
        """
        a = [1]
        b = [1]
        c = [2]
        d = [3]
        """
    )
    run_cell(
        """
        def func(x, y=a):
            e = [c[0] + d[0]]
            f = [x[0] + y[0]]
            return f
        """
    )
    run_cell("z = func(c)")
    run_cell("a = [4]")
    run_cell("logging.info(z[0])")
    assert_detected("Should have detected stale dependency of fn func on a")
    run_cell(
        """
        def func(x, y=a):
            logging.info(b[0])
            e = [c[0] + d[0]]
            f = [x[0] + y[0]]
            %flow show_deps f[0]
            return f
        # z = func(c)
        """
    )
    run_cell("z = func(c)")
    run_cell("%flow show_deps z[0]")
    run_cell("logging.info(z[0])")
    assert_not_detected()
    run_cell("logging.info(z)")
    assert_not_detected()
    run_cell("c = [3]")
    run_cell("logging.info(z)")
    assert_detected("Should have detected stale dependency of z on c")
    run_cell("c = [77]")
    run_cell("logging.info(z[0])")
    assert_detected("Should have detected stale dependency of z on c")
    run_cell("z = func(c)")
    run_cell("logging.info(z[0])")
    assert_not_detected()
    run_cell("logging.info(z)")
    assert_not_detected()
    run_cell("b = [40]")
    run_cell("d = [4]")
    run_cell("logging.info(z[0])")
    assert_not_detected("Changing b and d should not affect z")
    run_cell("logging.info(z)")
    assert_not_detected("Changing b and d should not affect z")


def test_func_assign_ints():
    run_cell(
        """
        a = 1
        b = 1
        c = 2
        d = 3
        def func(x, y=a):
            e = c + d
            f = x + y
            return f
        """
    )
    run_cell("z = func(c)")
    run_cell("a = 4")
    run_cell("logging.info(z)")
    assert_detected("Should have detected stale dependency of fn func on a")
    run_cell(
        """
        def func(x, y=a):
            logging.info(b)
            e = c + d
            f = x + y
            return f
        z = func(c)
        """
    )
    run_cell("logging.info(z)")
    assert_not_detected()
    run_cell("c = 3")
    run_cell("logging.info(z)")
    assert_detected("Should have detected stale dependency of z on c")
    run_cell("z = func(c)")
    run_cell("logging.info(z)")
    assert_not_detected()
    run_cell("b = 4")
    run_cell("d = 1")
    assert_not_detected("Changing b and d should not affect z")


def test_func_assign_helper_func():
    run_cell(
        """
        x = 3
        a = 4
        def f():
            def g():
                logging.info(a)
                return x
            return g()
        y = f()
        """
    )
    run_cell("x = 4")
    run_cell("logging.info(y)")
    assert_detected("Should have detected stale dependency of y on x")
    run_cell("y = f()")
    run_cell("logging.info(y)")
    assert_not_detected()
    run_cell("a = 1")
    run_cell("logging.info(y)")
    assert_not_detected("Changing a should not affect y")


def test_func_assign_helper_func_2():
    run_cell(
        """
        x = 3
        a = 4
        def f():
            def g():
                logging.info(a)
                return x
            return g
        y = f()()
        """
    )
    run_cell("x = 4")
    run_cell("logging.info(y)")
    assert_detected("Should have detected stale dependency of y on x")


def test_branching():
    run_cell("y = 7")
    run_cell("x = y + 3")
    run_cell(
        """
        if True:
            b = 5
        else:
            y = 7
        """
    )
    run_cell("logging.info(x)")
    assert_not_detected("false positive on unchanged y")


def test_branching_2():
    run_cell("y = 7")
    run_cell("x = y + 3")
    run_cell(
        """
        if False:
            b = 5
        else:
            y = 9
        """
    )
    run_cell("logging.info(x)")
    assert_detected("x depends on stale y")


def test_identity_checking():
    run_cell("y = 7")
    run_cell("x = y + 3")
    run_cell("y = 7")
    run_cell("logging.info(x)")
    assert_not_detected("`y` was not mutated")


def test_identity_checking_obj():
    # To get this working properly, we need to create datasyms for all of the values in the literal
    run_cell("y = [7]")
    run_cell("x = y + [3]")
    run_cell("y[0] = 7")
    run_cell("logging.info(x)")
    assert_not_detected("`y` was not mutated")


def test_identity_checking_obj_2():
    run_cell("x, y = [7], [8]")
    run_cell("z = y + [3]")
    run_cell("x[0] = 99")
    run_cell("logging.info(z)")
    assert_not_detected("`z` independent of x")
    run_cell("y[0] = 8")
    run_cell("logging.info(z)")
    assert_not_detected("`y` was not mutated")
    run_cell("y[0] = 42")
    run_cell("logging.info(z)")
    assert_detected("`y` was mutated")


def test_identity_checking_obj_3():
    run_cell('d = {"y": 7}')
    run_cell("x = list(d.values()) + [3]")
    run_cell('d["y"] = 7')
    run_cell("logging.info(x)")
    assert_not_detected('`d["y"]` was not mutated')
    run_cell('d["y"] = 8')
    run_cell("logging.info(x)")
    assert_detected('`d["y"]` was mutated')


def test_identity_checking_obj_4():
    # To get this working properly, we need to create datasyms for literal namespaces recursively
    run_cell('d = {"y": [7]}')
    run_cell('d["x"] = d["y"] + [3]')
    run_cell('d["y"][0] = 7')
    run_cell('logging.info(d["x"])')
    assert_not_detected('`d["y"]` was not mutated')
    run_cell('d["y"][0] = 8')
    run_cell('logging.info(d["x"])')
    assert_detected('`d["y"]` was mutated')


def test_identity_checking_obj_5():
    # To get this working properly, we need to create datasyms for literal namespaces recursively
    run_cell('d = {"y": {0: 7}}')
    run_cell('d["x"] = {1:3, **d["y"]}')
    run_cell('d["y"][0] = 7')
    run_cell('logging.info(d["x"])')
    assert_not_detected('`d["y"]` was not mutated')
    run_cell('d["y"][0] = 8')
    run_cell('logging.info(d["x"])')
    assert_detected('`d["y"]` was mutated')


def test_identity_checking_obj_6():
    # To get this working properly, we need to create datasyms for literal namespaces recursively
    run_cell("lst = [[1, 2], 0]")
    run_cell("lst[1] = lst[0] + [3, 4]")
    run_cell("lst[0][1] = 2")
    run_cell("logging.info(lst[1])")
    assert_not_detected("`lst[0][1]` was not mutated")
    run_cell("lst[0][1] = 42")
    run_cell("logging.info(lst[1])")
    assert_detected("`lst[0][1]` was mutated")


def test_starred_assignment_rhs():
    run_cell("x = 0")
    run_cell("y = 1")
    run_cell("z = 2")
    run_cell('lst = ["foo", "bar"]')
    # just to make sure the tracer can handle a starred expr in list literal
    run_cell("a, b, c, d, e = [x + 1, y + 2, z + 3, *lst]")
    run_cell("z = 42")
    run_cell("logging.info(a)")
    assert_not_detected()
    run_cell("logging.info(b)")
    assert_not_detected()
    run_cell("logging.info(c)")
    assert_detected()
    run_cell("logging.info(d)")
    assert_not_detected()
    run_cell("logging.info(e)")
    assert_not_detected()
    run_cell("lst[0] = 7")
    run_cell("logging.info(d)")
    assert_detected()
    run_cell("logging.info(e)")
    assert_not_detected()
    run_cell("lst[1] = 8")
    run_cell("logging.info(e)")
    assert_detected()


def test_starred_assignment():
    run_cell("x = 0")
    run_cell("y = 1")
    run_cell("z = 2")
    run_cell('lst = ["foo", "bar"]')
    # just to make sure the tracer can handle a starred expr in list literal
    run_cell("s, *t = [x + 1, y + 2, z + 3, *lst]")
    run_cell("z = 42")
    run_cell("logging.info(s)")
    assert_not_detected()
    run_cell("logging.info(t[0])")
    assert_not_detected()
    run_cell("logging.info(t[1])")
    assert_detected()
    run_cell("x = 99")
    run_cell("logging.info(s)")
    assert_detected()
    run_cell("logging.info(t[0])")
    assert_not_detected()
    run_cell("y = 142")
    run_cell("logging.info(t[0])")
    assert_detected()


def test_starred_assignment_in_middle():
    run_cell("a, b, c, d, e = 1, 2, 3, 4, 5")
    run_cell("x, *star, y = [a, b, c, d, e]")
    run_cell("a += 1")
    run_cell("logging.info(x)")
    assert_detected()
    run_cell("logging.info(star)")
    assert_not_detected()
    run_cell("logging.info(y)")
    assert_not_detected()
    run_cell("e += 1")
    run_cell("logging.info(y)")
    assert_detected()
    run_cell("logging.info(star)")
    assert_not_detected()
    run_cell("c += 1")
    run_cell("logging.info(star)")
    assert_detected()


def test_attributes():
    run_cell(
        """
        class Foo:
            def __init__(self, x):
                self.x = x
        """
    )
    run_cell("x = Foo(5)")
    run_cell("y = x.x + 5")
    run_cell("x.x = 8")
    run_cell("logging.info(y)")
    assert_detected("y depends on stale attrval x.x")


@skipif_known_failing
def test_attributes_2():
    run_cell(
        """
        class Foo:
            def __init__(self, x):
                self.x = x
        """
    )
    run_cell("x = Foo(5)")
    run_cell("y = x.x + 5")
    run_cell("%flow show_deps x")
    run_cell("%flow show_deps y")
    run_cell("x = 8")
    run_cell("logging.info(y)")
    assert_detected("y depends on stale x")


def test_attribute_unpacking():
    run_cell(
        """
        class Foo:
            def __init__(self, x):
                self.x = x
        """
    )
    run_cell("x = Foo(5)")
    run_cell("y = Foo(6)")
    run_cell("w = 42")
    run_cell("z = 43")
    run_cell("x.x, y.x = w + 2, z + 3")
    run_cell("z = 9001")
    run_cell("logging.info(x.x)")
    assert_not_detected()
    run_cell("logging.info(x)")
    assert_not_detected()
    run_cell("logging.info(y.x)")
    assert_detected()
    run_cell("logging.info(y)")
    assert_detected()
    run_cell("y.x = z + 3")
    run_cell("logging.info(y.x)")
    assert_not_detected()
    run_cell("w = 99")
    run_cell("logging.info(x.x)")
    assert_detected()
    run_cell("logging.info(x)")
    assert_detected()
    run_cell("logging.info(y.x)")
    assert_not_detected()
    run_cell("logging.info(y)")
    assert_not_detected()


def test_attribute_unpacking_no_overwrite():
    run_cell(
        """
        class Foo:
            def __init__(self, x):
                self.x = x
        """
    )
    run_cell("x = Foo(5)")
    run_cell("y = Foo(6)")
    run_cell("w = 42")
    run_cell("z = 43")
    run_cell("x.x, y.x = w + 2, z + 3")
    run_cell("s, t = 12, 13")
    run_cell("x.x, y.x = x.x + s, y.x + t")
    run_cell("w = 101")
    run_cell("logging.info(x.x)")
    assert_detected()
    run_cell("logging.info(y.x)")
    assert_not_detected()
    run_cell("z = 103")
    run_cell("logging.info(y.x)")
    assert_detected()


def test_attributes_3():
    run_cell(
        """
        class Foo:
            def __init__(self, x, y):
                self.x = x
                self.y = y
        """
    )
    run_cell("foo = Foo(5, 6)")
    run_cell("bar = Foo(7, 8)")
    run_cell("y = bar.x + 5")
    run_cell("foo.x = 8")
    run_cell("logging.info(y)")
    assert_not_detected("y does not depend on updated attrval foo.y")


def test_stale_use_of_attribute():
    run_cell(
        """
        class Foo:
            def __init__(self, x, y):
                self.x = x
                self.y = y
        """
    )
    run_cell("foo = Foo(5, 6)")
    run_cell("bar = Foo(7, 8)")
    run_cell("foo.x = bar.x + bar.y")
    run_cell("bar.y = 42")
    run_cell("logging.info(foo.x)")
    assert_detected("`foo.x` depends on stale `bar.y`")


def test_attr_manager_active_scope_resets():
    run_cell(
        """
        y = 10
        class Foo:
            def f(self):
                y = 11
                return y
        def f():
            return y
        """
    )
    run_cell("foo = Foo()")
    # if the active scope doesn't reset after done with foo.f(),
    # it will think the `y` referred to by f() is the one in Foo.f's scope.
    run_cell("x = foo.f() + f()")
    run_cell("y = 42")
    run_cell("logging.info(x)")
    assert_detected("`x` depends on stale `y`")


def test_attribute_call_point():
    run_cell(
        """
        y = 10
        class Foo:
            def f(self):
                return y
        """
    )
    run_cell("foo = Foo()")
    run_cell("x = foo.f()")
    run_cell("y = 42")
    run_cell("logging.info(x)")
    assert_detected("`x` depends on stale `y`")


def test_attr_manager_active_scope_with_property():
    run_cell(
        """
        y = 10
        class Foo:
            @property
            def f(self):
                y = 11
                return y
        """
    )
    run_cell("foo = Foo()")
    # if the active scope doesn't reset after done with foo.f(),
    # it will think the `y` referred to by f() is the one in Foo.f's scope.
    run_cell("x = foo.f")
    run_cell("y = 42")
    run_cell("logging.info(x)")
    assert_not_detected("`x` independent of outer `y`")


def test_namespace_scope_resolution():
    run_cell(
        """
        y = 42
        class Foo:
            y = 10
            @property
            def foo(self):
                return y
        """
    )
    run_cell("foo = Foo()")
    run_cell("x = foo.foo")
    run_cell("Foo.y = 99")
    run_cell("logging.info(x)")
    assert_not_detected("`x` should not have dependency on `Foo.y`")


def test_long_chain_attribute():
    run_cell(
        """
        class Foo:
            shared = 99
            def __init__(self, x, y):
                self.x = x
                self.y = y + self.shared

            class Bar:
                def __init__(self, a, b):
                    self.a = a
                    self.b = b

                def foo(self):
                    return Foo(self.a, self.b)

                def bar(self):
                    return Foo
        """
    )
    run_cell("foo = Foo(5, 6)")
    run_cell("bar = Foo.Bar(7, 8)")
    run_cell("foo.x = 42")
    run_cell("logging.info(bar.a)")
    assert_not_detected()
    run_cell("Foo.Bar(9, 10).foo().shared = 100")
    run_cell(
        """
        for _ in range(10**2):
            Foo.Bar(9, 10).foo().shared = 100
        """
    )
    run_cell("logging.info(foo.y)")
    assert_not_detected(
        "we mutated `shared` on obj and not on `Foo` (the one on which `foo.y` depends)"
    )
    run_cell("Foo.Bar(9, 10).bar().shared = 100")
    run_cell("logging.info(foo.y)")
    assert_detected("we mutated a shared value on which `foo.y` depends")


def test_numpy_subscripting():
    run_cell("import numpy as np")
    run_cell("x = np.zeros(5)")
    run_cell("y = x[3] + 5")
    run_cell("x[3] = 2")
    run_cell("logging.info(y)")
    assert_detected("y depends on stale x[3]")


def test_tracing_reactivated_after_import():
    run_cell("x = 0")
    run_cell(
        """
        def test():
            import numpy
            y = x + 1
            return y
        """
    )
    run_cell("y = test()")
    run_cell("x = 42")
    run_cell("logging.info(y)")
    assert_detected("y depends on stale x")


def test_dict_subscripting():
    run_cell('d = {"foo": "bar", 0: "bat"}')
    run_cell("x = 7")
    run_cell("d[0] = x")
    run_cell("x += 1")
    run_cell('z = d["foo"] + " asdf"')
    assert_not_detected('`d["foo"]` does not depend on stale `x`, unlike `d[0]`')
    run_cell("logging.info(z)")
    assert_not_detected()
    run_cell('logging.info(d["foo"])')
    assert_not_detected()


def test_dict_subscripting_2():
    run_cell('d = {"foo": "bar", 0: "bat"}')
    run_cell("x = 7")
    run_cell("d[0] = x")
    run_cell("x += 1")
    run_cell("d[0] = x")
    run_cell("logging.info(d)")
    assert_not_detected("we updated the stale entry of `d`")


def test_pandas_attr_mutation_with_alias():
    run_cell("import pandas as pd")
    run_cell('df = pd.DataFrame({"a": [0,1], "b": [2., 3.]})')
    run_cell("asdf = df.b")
    run_cell("df.b *= 7")
    run_cell("logging.info(asdf)")
    assert_detected(
        "`asdf` has same values as `df.b`, but they now point to different things, "
        "which in general could be dangerous"
    )


def test_stale_detection_works_when_namespace_available_but_stale_symbol_unavailable():
    run_cell("import pandas as pd")
    run_cell('data = {"a": list(range(5)), "b": list(range(1, 6))}')
    run_cell("df = pd.DataFrame(data)")
    run_cell("df.b")  # force creation of a namespace for df
    run_cell('data = {"a": list(range(4)), "b": list(range(1, 5))}')
    run_cell("logging.info(df.a)")
    assert_detected("`df.a` depends on stale `data` dictionary")


def test_stale_detection_works_when_namespace_available_but_stale_symbol_unavailable_2():
    run_cell("import pandas as pd")
    run_cell('data = {"a": list(range(5)), "b": list(range(1, 6))}')
    run_cell("df = pd.DataFrame(data)")
    run_cell("df.b")  # force creation of a namespace for df
    run_cell('data["b"] = 42')
    run_cell("logging.info(df.a)")
    assert_false_positive('`df.a` independent of entry `data["b"]`')


def test_list_alias_breaking():
    run_cell("x = [0]")
    run_cell("y = x")
    run_cell("x = [7]")
    run_cell("logging.info(y)")
    assert_detected("`y` has stale dependency on old `x`")


def test_list_alias_no_break():
    run_cell("x = [0]")
    run_cell("y = x")
    run_cell("x *= 7")
    run_cell("logging.info(y)")
    assert_not_detected("`y` still aliases `x`")


@skipif_known_failing
def test_subscript_sensitivity():
    run_cell("lst = list(range(5))")
    run_cell("i = 0")
    run_cell("lst[i] = 10")
    run_cell("i = 1")
    run_cell("logging.info(lst)")
    assert_detected("`lst[0]` depends on stale i")


def test_subscript_adds():
    run_cell("x = 42")
    run_cell(
        """
        d = {
            'foo': x,
            'bar': 77
        }
        """
    )
    run_cell('d["bar"] = 99')
    run_cell("x = 100")
    run_cell("logging.info(d)")
    assert_detected("`d` depends on stale `x`")


def test_list_mutation():
    run_cell("lst = list(range(5))")
    run_cell("x = 42")
    run_cell("asdf = []")
    run_cell("asdf.append(lst.append(x))")
    run_cell("x = 43")
    run_cell("logging.info(lst)")
    assert_detected("lst depends on stale x")


def test_list_mutation_extend():
    run_cell("lst = [0, 1]")
    run_cell("x = lst[0] + 7")
    run_cell("lst.extend([2, 3, 4, x])")
    run_cell("logging.info(x)")
    assert_not_detected()
    run_cell("x = 77")
    run_cell("logging.info(lst)")
    assert_detected("`lst` depends on stale `x`")


def test_list_mutation_2():
    run_cell("lst = list(range(5))")
    run_cell("x = lst + [42, 43]")
    run_cell("lst.append(99)")
    run_cell("logging.info(x)")
    assert_detected("`x` depends on stale `lst`")


def test_list_mutation_from_attr():
    run_cell('s = "hello X world X how X are X you X today?"')
    run_cell("lst = []")
    run_cell(
        """
        for word in s.split('X'):
            lst.append(word.strip())
        """
    )
    run_cell('s = "foobar"')
    run_cell("logging.info(lst)")
    assert_detected("`lst` depends on stale `s`")


@skipif_known_failing
def test_list_mutation_extend_from_attr():
    # for this one, we somehow have to avoid disabling tracing which is hard
    run_cell('s = "hello X world X how X are X you X today?"')
    run_cell("lst = []")
    run_cell('lst.extend(word.strip() for word in s.split("X"))')
    run_cell('s = "foobar"')
    run_cell("logging.info(lst)")
    assert_detected("`lst` depends on stale `s`")


def test_lazy_class_scope_resolution():
    run_cell(
        """
        class Foo:
            shared = 99
            def __init__(self, x):
                self.x = x
        """
    )
    run_cell("foo = Foo(10)")
    run_cell("y = 11")
    run_cell("Foo.shared = y + 42")
    run_cell("y = 12")
    run_cell("logging.info(foo.shared)")
    assert_detected(
        "`foo.shared` should point to same DataSymbol as `Foo.shared` and thus also has stale dep"
    )
    run_cell("foo.shared = 89")
    run_cell("logging.info(Foo.shared)")
    assert_detected(
        "Even though we refreshed `foo.shared`, this "
        "has no bearing on the original class member `Foo.shared`"
    )


def test_new_scope_val_depends_on_old():
    run_cell(
        """
        class Foo:
            shared = 99
        """
    )
    run_cell("foo = Foo()")
    run_cell("foo.shared = 11")
    run_cell("foo_shared_alias = foo.shared")
    run_cell("Foo.shared = 12")
    run_cell("logging.info(foo_shared_alias)")
    assert_detected()
    run_cell("logging.info(foo.shared)")
    assert_detected()
    run_cell("logging.info(foo)")
    assert_detected()


def test_class_member_mutation_does_not_affect_instance_members():
    run_cell(
        """
        class Foo:
            shared = 99
            def __init__(self):
                self.x = 42
        """
    )
    run_cell("foo = Foo()")
    run_cell("Foo.shared = 12")
    run_cell("logging.info(foo.x)")
    assert_not_detected()


def test_numpy_subscripting_fp():
    run_cell("import numpy as np")
    run_cell("x = np.zeros(5)")
    run_cell("y = x[3] + 5")
    run_cell("x[0] = 2")
    run_cell("logging.info(y)")
    assert_not_detected("`y` depends on unchanged `x[3]` and not on changed `x[0]`")


def test_old_format_string():
    run_cell("a = 5\nb = 7")
    run_cell('expr_str = "{} + {} = {}".format(a, b, a + b)')
    run_cell("a = 9")
    run_cell("logging.info(expr_str)")
    assert_detected("`expr_str` depends on stale `a`")


def test_old_format_string_kwargs():
    run_cell("a = 5\nb = 7")
    run_cell('expr_str = "{a} + {b} = {total}".format(a=a, b=b, total=a + b)')
    run_cell("a = 9")
    run_cell("logging.info(expr_str)")
    assert_detected("`expr_str` depends on stale `a`")


def test_new_format_string():
    run_cell("a = 5\nb = 7")
    run_cell('expr_str = f"{a} + {b} = {a+b}"')
    run_cell("a = 9")
    run_cell("logging.info(expr_str)")
    assert_detected("`expr_str` depends on stale `a`")


def test_scope_resolution():
    run_cell(
        """
        def f(x):
            def g(x):
                return 2 * x
            return g(x) + 8
        """
    )
    run_cell("x = 7")
    run_cell("y = f(x)")
    run_cell("x = 8")
    run_cell("logging.info(y)")
    assert_detected("`y` depends on stale `x`")


def test_scope_resolution_2():
    run_cell(
        """
        def g(x):
            return 2 * x
        def f(x):
            return g(x) + 8
        """
    )
    run_cell("x = 7")
    run_cell("y = f(x)")
    run_cell("x = 8")
    run_cell("logging.info(y)")
    assert_detected("`y` depends on stale `x`")


def test_funcall_kwarg():
    run_cell(
        """
        def f(y):
            return 2 * y + 8
        """
    )
    run_cell("x = 7")
    run_cell("z = f(y=x)")
    run_cell("x = 8")
    run_cell("logging.info(z)")
    assert_detected("`z` depends on stale `x`")


def test_funcall_kwarg_2():
    run_cell(
        """
        def f(y):
            return 2 * y + 8
        """
    )
    run_cell("x = 7")
    run_cell("y = f(y=x)")
    run_cell("x = 8")
    run_cell("logging.info(y)")
    assert_detected("`y` depends on stale `x`")


def test_funcall_kwarg_3():
    run_cell(
        """
        def f(x):
            return 2 * x + 8
        """
    )
    run_cell("x = 7")
    run_cell("y = f(x=x)")
    run_cell("x = 8")
    run_cell("logging.info(y)")
    assert_detected("`y` depends on stale `x`")


def test_funcall_kwarg_4():
    run_cell(
        """
        def f(x):
            return 2 * x + 8
        """
    )
    run_cell("x = 7")
    run_cell("x = f(x=x)")
    run_cell("x = 8")
    run_cell("logging.info(x)")
    assert_not_detected("`x` is overriden so should not be stale")


def test_single_line_dictionary_literal():
    run_cell("foo = 5")
    run_cell("bar = 6")
    run_cell('d = {foo: bar, "pi": 42,}')
    run_cell("bar = 7")
    run_cell("logging.info(d)")
    assert_detected("`d` depends on stale `bar`")


def test_single_line_dictionary_literal_fix_stale_deps():
    run_cell("foo = 5")
    run_cell("bar = 6")
    run_cell('d = {foo: bar, "pi": 42,}')
    run_cell("bar = 7")
    run_cell("logging.info(d)")
    assert_detected("`d` depends on stale `bar`")
    run_cell("d[foo] = bar")
    assert_not_detected()
    run_cell("logging.info(d)")
    # assert_false_positive('`d`s stale dep fixed, but this is hard to detect '
    #                       'since we did not yet have a DataSymbol for `d[foo]` when staleness introduced')
    assert_not_detected("`d`s stale dep fixed")
    run_cell("foo = 8")
    run_cell("logging.info(d)")
    assert_detected("`d` depends on stale `foo`")
    # TODO: not sure what the correct behavior for the below write to d[foo] after foo changed should be
    # run_cell('d[foo] = bar')
    # run_cell('logging.info(d)')
    # assert_not_detected('`d`s stale dep fixed')


def test_multiline_dictionary_literal():
    run_cell("foo = 5")
    run_cell("bar = 6")
    run_cell(
        """
        d = {
            foo: bar,
            'pi': 42,
        }
        """
    )
    run_cell("bar = 7")
    run_cell("logging.info(d)")
    assert_detected("`d` depends on stale `bar`")


def test_exception():
    run_cell("lst = list(range(5))")
    run_cell("x = 6")
    run_cell(
        """
        try:
            lst[x] = 42
        except:
            lst[0] = 42
        """
    )
    run_cell("x = 7")
    run_cell("logging.info(lst)")
    assert_not_detected("lst should be independent of x due to exception")


def test_for_loop_binding():
    run_cell("a = 0")
    run_cell("b = 1")
    run_cell("c = 2")
    run_cell("lst = [a, b, c]")
    run_cell(
        """
        for i in lst:
            pass
        """
    )
    run_cell("a = 3")
    run_cell("logging.info(i)")
    assert_false_positive(
        "`i` should not depend on `a` at end of for loop but this is hard"
    )


@skipif_known_failing
def test_for_loop_literal_binding():
    run_cell("a = 0")
    run_cell("b = 1")
    run_cell("c = 2")
    run_cell(
        """
        for i in [a, b, c]:
            pass
        """
    )
    run_cell("a = 3")
    run_cell("logging.info(i)")
    assert_not_detected("`i` should not depend on `a` at end of for loop")


def test_for_loop_partial_dep():
    run_cell("lst = list(range(10))")
    run_cell("s = 0")
    run_cell(
        """
        for i in range(5):
            s += lst[i]
        """
    )
    run_cell("lst[-1] = 42")
    run_cell("logging.info(s)")
    assert_not_detected("`s` does not depend on last entry of `lst`")
    run_cell("lst[1] = 22")
    run_cell("logging.info(s)")
    assert_false_negative(
        "`s` does depend on second entry of `lst` but tracing every iteration of loop is slow"
    )


def test_for_loop_tuple_unpack():
    run_cell("x = (1, 2)")
    run_cell("y = (3, 4)")
    run_cell("total_i = 0")
    run_cell("total_j = 0")
    run_cell(
        """
        for i, j in [x, y]:
            total_i += i
            total_j += j
        """
    )
    run_cell("logging.info(total_i)")
    assert_not_detected()
    run_cell("logging.info(total_j)")
    assert_not_detected()
    run_cell("x = (42, 43)")
    run_cell("logging.info(total_i)")
    assert_detected("`total_i` depends on old version of `x`")
    run_cell("logging.info(total_j)")
    assert_detected("`total_j` also depends on old version of `x`")


def test_same_cell_redefine():
    run_cell("a = 0")
    run_cell(
        """
        b = a + 1
        a = 42
        """
    )
    run_cell("logging.info(b)")
    assert_not_detected(
        "`b` should not be considered as having stale dependency since `a` changed in same cell as `b`"
    )


def test_multiple_stmts_in_one_line():
    run_cell("a = 1; b = 2")
    run_cell("x = a + b")
    run_cell("a = 42")
    run_cell("logging.info(x)")
    assert_detected("`x` depends on stale value of `a`")


def test_multiple_stmts_in_one_line_2():
    run_cell("a = 1; b = 2")
    run_cell("x = a + b")
    run_cell("b = 42")
    run_cell("logging.info(x)")
    assert_detected("`x` depends on stale value of `a`")


def test_line_magic():
    run_cell(
        """
        %lsmagic
        %lsmagic
        %lsmagic
        %lsmagic
        %lsmagic
        a = 0
        """
    )
    run_cell(
        """
        %lsmagic
        %lsmagic
        %lsmagic
        %lsmagic
        x = a + 1
        """
    )
    run_cell(
        """
        %lsmagic
        %lsmagic
        %lsmagic
        a = 42
        """
    )
    run_cell(
        """
        %lsmagic
        %lsmagic
        logging.info(x)
        """
    )
    assert_detected("`x` depends on stale value of `a`")
    run_cell(
        """
        %lsmagic
        logging.info(x)
        %lsmagic
        """
    )
    assert_detected("`x` depends on stale value of `a`")


def test_exception_stack_unwind():
    def assert_stack_size(size):
        len_call_stack = "len(tracer().call_stack)"
        return ", ".join(
            [
                f"assert {len_call_stack} == {size}",
                f'"%d vs {size}" % {len_call_stack}',
            ]
        )

    run_cell(
        f"""
        import numpy as np
        from ipyflow.singletons import tracer
        {assert_stack_size(0)}
        def f():
            {assert_stack_size(1)}
            def g():
                {assert_stack_size(2)}
                def h():
                    {assert_stack_size(3)}
                    return np.loadtxt('does-not-exist.txt')
                return h()
            try:
                return g()
            except:
                {assert_stack_size(1)}
        f()
        {assert_stack_size(0)}
        """
    )


def test_throwing_statements_do_not_track_deps():
    run_cell(
        """
        z = 10
        def foo():
            def bar():
                raise ValueError('foo!')
            return bar() + z
        x = 0
        y = x + 1
        """
    )
    run_cell(
        """
        try:
            x = 42 + foo()
        except:
            pass
        """
    )
    run_cell("logging.info(y)")
    assert_not_detected("no stale dep for `y` because update on `x` threw exception")
    run_cell("z = 99")
    run_cell("logging.info(x)")
    assert_not_detected(
        "no stale dep for `x` because it is indep of `z` (attempted dep add threw)"
    )


def test_attr_dep_from_somewhere_else():
    run_cell("import fakelib as fake")
    run_cell("fake.y = 7")
    run_cell("x = fake.y + 1")
    run_cell("fake.y = 42")
    run_cell("logging.info(x)")
    assert_detected("`x` depends on old value of `fake.y`")


def test_attr_use_from_somewhere_else():
    run_cell("import fakelib as fake")
    run_cell("x = 7")
    run_cell("fake.y = x + 1")
    run_cell("x = 42")
    run_cell("logging.info(fake.y)")
    assert_detected("`fake.y` depends on old value of `x`")


def test_class_assignment():
    run_cell(
        """
        class Foo:
            def __init__(self):
                self.y = 99

        Bar = Foo
        foo = Bar()
        x = 7
        """
    )
    run_cell("foo.y = x + 1")
    run_cell("x = 42")
    run_cell("logging.info(foo.y)")
    assert_detected("`foo.y` depends on stale `x`")
    run_cell("foo.y = 10")
    run_cell("x = foo.y + 1")
    run_cell("foo.y = 12")
    run_cell("logging.info(x)")
    assert_detected("`x` depends on stale `foo.y`")


def test_no_class_false_positives():
    run_cell("x = 7")
    run_cell("y = x + 1")
    run_cell(
        """
        class Foo:
            x = 42
        """
    )
    run_cell("logging.info(y)")
    assert_not_detected("x inside class scope is different")


def test_set_literal():
    run_cell("x, y, z = 1, 2, 3")
    run_cell("s = {x + 1, y + 7}")
    run_cell("z = 42")
    run_cell("logging.info(s)")
    assert_not_detected()
    run_cell("x = 17")
    run_cell("logging.info(s)")
    assert_detected()


def test_tuple_unpack_simple():
    run_cell("x, y = 0, 1")
    run_cell("a, b = x + 2, y + 3")
    run_cell("x, y = 42, 43")
    run_cell("logging.info(a)")
    assert_detected("`a` depends on stale `x`")
    run_cell("logging.info(b)")
    assert_detected("`b` depends on stale `y`")


def test_tuple_unpack_hard():
    run_cell("x, y = 0, 1")
    run_cell("a, b = x + 2, y + 3")
    run_cell("y = 43")
    run_cell("logging.info(a)")
    assert_not_detected("`a` does not depend on `y`")
    run_cell("logging.info(b)")
    assert_detected("`b` depends on stale `y`")
    run_cell("b = y + 10")
    run_cell("x = 99")
    run_cell("logging.info(b)")
    assert_not_detected("`b` does not depend on `x`")
    run_cell("logging.info(a)")
    assert_detected("`a` depends on stale `x`")


def test_unpack_multiple_from_single():
    run_cell("x, y = 0, 1")
    run_cell("lst = [x + 1, y + 1]")
    run_cell("a, b = lst")
    run_cell("x = 42")
    run_cell("logging.info(b)")
    assert_not_detected()
    run_cell("logging.info(a)")
    assert_detected()


@skipif_known_failing
def test_attr_dep_with_top_level_overwrite():
    run_cell(
        """
        class Foo:
            def __init__(self):
                self.y = 99
        foo = Foo()
        x = 42
        foo.y = x + 7
        """
    )
    run_cell("x = 43")
    run_cell("logging.info(foo)")  # this should be a 'deep' usage of foo
    assert_detected("logging.info could display `foo.y` which depends on x")
    run_cell("foo.y = 70")
    assert_not_detected("we just fixed stale dep of `foo.y` by changing its deps")
    run_cell("x = foo.y + 7")
    run_cell("foo = 81")
    run_cell("logging.info(x)")
    assert_detected("`x` has stale dep on `foo` (transitively through `foo.y`)")


def test_typed_assignment():
    run_cell("a = 0")
    run_cell("b: int = a + 1")
    run_cell("a: int = 42")
    run_cell("logging.info(b)")
    assert_detected("`b` has stale dep on `a`")


@skipif_known_failing
def test_time_line_magic():
    run_cell("a = 0")
    run_cell("b = a + 1")
    run_cell("%time a = 42")
    run_cell("logging.info(b)")
    assert_detected("`b` has stale dep on `a`")


@skipif_known_failing
def test_cell_magic():
    run_cell("a = 0")
    run_cell("b = a + 1")
    run_cell("%%time\na = 42")
    run_cell("logging.info(b)")
    assert_detected("`b` has stale dep on `a`")


def test_pandas():
    run_cell("import numpy as np")
    run_cell("import pandas as pd")
    run_cell("arr = 1 + np.arange(10)")
    run_cell('df = pd.DataFrame({"col": arr})')
    run_cell("df2 = df.dropna()")
    run_cell("df.dropna()")
    run_cell("logging.info(df2)")
    assert_not_detected("`df.dropna()` did not mutate `df`")
    run_cell("df.dropna(inplace=True)")
    run_cell("logging.info(df2)")
    assert_detected("`df.dropna(inplace=True)` mutated `df`")


def test_deeply_nested_arg_and_kwarg_refs_with_attr_calls():
    run_cell(
        """
        class Foo:
            def foo(self, x, y=0):
                return x + y
        """
    )
    run_cell("x = 1")
    run_cell("y = 2")
    run_cell("foo = Foo()")
    run_cell("z = foo.foo(foo.foo(foo.foo(x, y=y), y=7), y=foo.foo(123))")
    run_cell("logging.info(z)")
    assert_not_detected()
    run_cell("x = 101")
    run_cell("logging.info(z)")
    assert_detected("`z` depends on stale `x`")
    run_cell("z = foo.foo(foo.foo(foo.foo(x, y=y), y=foo.foo(200)), y=99)")
    run_cell("logging.info(z)")
    assert_not_detected()
    run_cell("y = 102")
    run_cell("logging.info(z)")
    assert_detected("`z` depends on stale `y`")


def test_class_member_granularity():
    run_cell(
        """
        y = 77
        class Foo:
            def __init__(self):
                self.x = 10
                self.y = y
        """
    )
    run_cell("foo = Foo()")
    run_cell("z = foo.x + 7")
    run_cell("y = 42")
    run_cell("logging.info(z)")
    assert_not_detected("`z` independent of stale `y`")


def test_no_rhs_propagation():
    run_cell("import numpy as np")
    run_cell("x = np.random.random(10)")
    run_cell("y = np.random.random(10)")
    run_cell("inds = np.argsort(x)")
    run_cell("x = x[inds]")
    run_cell("y = y[inds]")
    assert_not_detected(
        "`inds` should not be considered stale since it appears on RHS of assignment"
    )


def test_if_true():
    run_cell("y = 0")
    run_cell("z = 42")
    run_cell(
        """
        if True:
            x = y + 1
        else:
            x = z + 1
        """
    )
    run_cell("z = 43")
    run_cell("logging.info(x)")
    assert_not_detected("`x` not dependent on `z`")
    run_cell("y = 99")
    run_cell("logging.info(x)")
    assert_detected("`x` dependent on old `y`")


def test_tracing_reenabled_after_dup_funcall():
    run_cell("x = 0")
    run_cell(
        """
        def f():
            return 42
        """
    )
    run_cell(
        """
        f()
        f()
        y = x + 1
        """
    )
    run_cell("x = 42")
    run_cell("logging.info(y)")
    assert_detected()


def test_one_time_tracing_func():
    run_cell("x = 0")
    run_cell("y = 1")
    run_cell(
        """
        def f(p):
            if p:
                return x
            else:
                return y
        """
    )
    run_cell("z = f(False) + 1\nz = f(True) + 1")
    run_cell("y = 2")
    run_cell("logging.info(z)")
    assert_not_detected()
    run_cell("x = 3")
    run_cell("logging.info(z)")
    assert_detected("tracing should not be disabled")


def test_tracing_disable_with_nested_calls():
    # run_cell('%flow trace_messages enable')
    run_cell("y = 0")
    run_cell(
        """
        def f():
            return y
        """
    )
    run_cell(
        """
        def g(flag):
            if flag:
                return f()
            else:
                return 2
        """
    )
    run_cell(
        """
        g(False)
        x = g(True) + 1
        """
    )
    run_cell("y = 42")
    run_cell("logging.info(x)")
    assert_detected("`x` has dep on stale `y`")


def test_dict():
    run_cell("d = {}; d[0] = 0")
    run_cell("x = d[0] + 1")
    run_cell("d[0] = 42")
    run_cell("logging.info(x)")
    assert_detected("`x` has dependency on old value of `d[0]`")


def test_dict_2():
    run_cell("d = {}; d[0] = 0")
    run_cell("x = d[0] + 1")
    run_cell("d = {}; d[0] = 42")
    run_cell("logging.info(x)")
    assert_not_detected(
        "`d` is an entirely new namespace so new d[0] is distinct from old"
    )


def test_default_dict():
    run_cell("from collections import defaultdict")
    run_cell("d = defaultdict(dict); d[0][0] = 0")
    run_cell("x = d[0][0] + 1")
    run_cell("d = defaultdict(dict); d[0][0] = 42")
    run_cell("logging.info(x)")
    assert_detected("`x` has dependency on old value of `d[0][0]`")


def test_mutate_arg():
    run_cell("import numpy as np")
    run_cell("x = np.ones(5)")
    run_cell("y = x + 1")
    run_cell("np.random.shuffle(x)")
    run_cell("logging.info(y)")
    assert_detected("`y` has a dependency on an old value of `x`")


def test_mutate_arg_special_cases():
    run_cell("import numpy as np")
    run_cell("x = np.random.random(10)")
    run_cell("y = np.ones(5)")
    run_cell("np.random.seed(42)")
    run_cell("logging.info(x)")
    assert_detected("`x` depends on previous seed")
    run_cell("logging.info(y)")
    assert_not_detected("`y` has no dependency on seed")
    run_cell("x = y + 5")
    run_cell("logging.info(y)")
    assert_not_detected()
    run_cell("logging.info(x)")
    assert_not_detected("`logging.info` does not mutate `y`")


def test_augassign_does_not_overwrite_deps():
    run_cell("x = 0")
    run_cell("y = 1")
    run_cell("z = x + 2")
    run_cell("z += y")
    run_cell("x = 42")
    run_cell("logging.info(z)")
    assert_detected("`z` depends on old value of `x`")


def test_rval_appearance_does_not_overwrite_deps():
    run_cell("x = 0")
    run_cell("y = 1")
    run_cell("z = x + 2")
    run_cell("z = z + y")
    run_cell("x = 42")
    run_cell("logging.info(z)")
    assert_detected("`z` depends on old value of `x`")


def test_augassign_does_not_overwrite_deps_for_attributes():
    run_cell(
        """
        class Foo:
            def __init__(self, x):
                self.x = x
        """
    )
    run_cell("x, y, z = Foo(0), Foo(5), Foo(10)")
    run_cell("z.x = x.x + 2")
    run_cell("z.x += y.x")
    run_cell("x.x = 42")
    run_cell("logging.info(z.x)")
    assert_detected("`z.x` depends on old value of `x.x`")


def test_rval_appearance_does_not_overwrite_deps_for_attributes():
    run_cell(
        """
        class Foo:
            def __init__(self, x):
                self.x = x
        """
    )
    run_cell("x, y, z = Foo(0), Foo(5), Foo(10)")
    run_cell("z.x = x.x + 2")
    run_cell("z.x = z.x + y.x")
    run_cell("x.x = 42")
    run_cell("logging.info(z.x)")
    assert_detected("`z.x` depends on old value of `x.x`")


def test_context_manager():
    run_cell(
        """
        from contextlib import contextmanager

        @contextmanager
        def foo():
            yield 42
        """
    )
    run_cell("with foo() as bar: x = bar + 7")
    run_cell("bar = 43")
    run_cell("logging.info(x)")
    assert_detected("`x` depends on old value of `bar`")


def test_decorator():
    run_cell("foo = lambda f: f")
    run_cell(
        """
        @foo
        def bar(x):
            return x + 42
        """
    )
    run_cell("y = bar(7)")
    run_cell("foo = lambda f: lambda x: f(x + 9)")
    run_cell("logging.info(y)")
    assert_detected("`y` depends on call to `bar` which has stale decorator `@foo`")


def test_magics_dont_break_things():
    run_cell(
        """
        %time dummy = 0
        x = 0
        y = x + 1
        """
    )
    run_cell("x = 42")
    run_cell("logging.info(y)")
    assert_detected("`y` depends on old value `x`")


def test_tuple_return():
    run_cell(
        """
        x = 0
        y = 1
        a = x + 42
        b = y + 77
        def foo():
            return a, b
        """
    )
    run_cell("t = foo()[1]")
    run_cell("x = 9")
    run_cell("logging.info(t)")
    assert_not_detected("`t` independent of updated `x`")
    run_cell("y = 10")
    run_cell("logging.info(t)")
    assert_detected("`t` depends on old version of `y`")


def test_tuple_return_obj():
    run_cell(
        """
        x = 0
        y = 1
        a = x + 42
        b = y + 77
        def foo():
            return [a], [b]
        """
    )
    run_cell("t = foo()[1][0]")
    run_cell("x = 9")
    run_cell("logging.info(t)")
    assert_not_detected("`t` independent of updated `x`")
    run_cell("y = 10")
    run_cell("logging.info(t)")
    assert_detected("`t` depends on old version of `y`")


def test_property_in_function_arg():
    run_cell(
        f"""
        import builtins
        z = 42
        class Foo:
            @property
            def bar(self):
                # ensures that this is not called during static analysis
                assert hasattr(builtins, "{EMIT_EVENT}")
                return z

        def f(x, y):
            return x + y
        """
    )
    run_cell("foo = Foo()")
    run_cell("w = f(3, foo.bar)")
    run_cell("z = 7")
    run_cell("logging.info(w)")
    assert_detected("`w` depends on old version of `z`")


def test_getter_setter_with_global():
    run_cell(
        """
        z = 42
        class Bar:
            shared = 25
            @property
            def baz(self):
                return z

            @baz.setter
            def baz(self, new_val):
                global z
                z = new_val

        class Foo:
            def __init__(self):
                self.bar = Bar()

        def f(x, y):
            return x + y
        """
    )
    run_cell("foo = Bar()")
    run_cell("w = f(3, foo.baz)")
    run_cell("logging.info(w)")
    assert_not_detected()
    run_cell("foo.baz = 84")
    run_cell("logging.info(w)")
    assert_detected("`w` depends on stale `foo.baz`")


def test_list_extend():
    run_cell("lst = [0, 1, 2]")
    run_cell("x = lst[1] + 1")
    run_cell("lst.extend([3, 4, 5, 6, 7, 8, 9])")
    run_cell("logging.info(x)")
    assert_not_detected()
    run_cell("logging.info(lst[1])")
    assert_not_detected()
    run_cell("y = lst[8] + 1")
    run_cell("lst[8] += 42")
    run_cell("logging.info(y)")
    assert_detected("`y` depends on stale `lst[8]`")


@skipif_known_failing
def test_list_sum_simple():
    run_cell("w, x, y, z = 0, 1, 2, 3")
    run_cell("lst = [w, x] + [y, z]")
    run_cell("z += 42")
    run_cell("logging.info(lst[0])")
    assert_not_detected()
    run_cell("logging.info(lst[1])")
    assert_not_detected()
    run_cell("logging.info(lst[2])")
    assert_not_detected()
    run_cell("logging.info(lst[3])")
    assert_detected()


def test_global_var():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell("def f(): global x; x = 42")
    run_cell("logging.info(y)")
    assert_not_detected()
    run_cell("f()")
    run_cell("logging.info(y)")
    assert_detected()


def test_nonlocal_var():
    cell_template = """
        z = 0
        def f():
            x = 0
            def g():
                {stmt}
                x = 42
            global z
            z = x
            return g
        g = f()
        """
    run_cell(cell_template.format(stmt="pass"))
    run_cell("y = z + 1")
    run_cell("g()")
    run_cell("logging.info(y)")
    assert_not_detected()
    run_cell(cell_template.format(stmt="nonlocal x"))
    run_cell("y = z + 1")
    run_cell("g()")
    run_cell("logging.info(y)")
    assert_detected()


def test_reimport_does_not_propagate():
    run_cell("import numpy as np")
    run_cell("arr = np.zeros(3)")
    run_cell("import numpy as np")
    assert_not_detected()


def test_pyccolo_exec():
    run_cell("x = 0")
    run_cell("y = x + 1")
    run_cell("import pyccolo as pyc; _ = pyc.exec('x = 42')")
    run_cell("logging.info(y)")
    assert_not_detected()


def test_pyccolo_exec_2():
    run_cell("lst = [42]")
    run_cell("lst2 = lst + [9001]")
    run_cell("import pyccolo as pyc; _ = pyc.exec('lst.append(55)')")
    run_cell("logging.info(lst2)")
    assert_not_detected()
    run_cell("assert lst == [42, 55]")


def test_nested_calls():
    run_cell("x = 42")
    run_cell("def bar(): return x + 1")
    run_cell("def foo(): return bar() + 7")
    run_cell("y = foo() + 9")
    run_cell("logging.info(y)")
    assert_not_detected()
    run_cell("x = 43")
    run_cell("logging.info(y)")
    assert_detected()


def test_underscore():
    run_cell("x = 42")
    run_cell("x")
    run_cell("y = _ + 1")
    run_cell("x = 43")
    run_cell("x")
    run_cell("logging.info(y)")
    assert_detected()


def test_annotations_random_module():
    run_cell("import random")
    run_cell("foo = type(random)")
    run_cell("random.seed(42)")
    run_cell("logging.info(foo)")
    assert_detected()


# TODO: where was I going with this?
# def test_getitem_call():
#     run_cell("""
# class Foo:
#     def __getitem__(self, x):
#         return 42
# """)
#     run_cell('foo = Foo()')
#     run_cell('x = foo[0]')


if sys.version_info >= (3, 8):

    def test_walrus_simple():
        run_cell(
            """
            if (x := 1) > 0:
                y = x + 1
            """
        )
        run_cell("x = 42")
        run_cell("logging.info(y)")
        assert_detected("`y` depends on old value of `x`")

    def test_walrus_fancy():
        run_cell(
            """
            if (x := (y := (z := 1) + 1) + 1) > 0:
                a = x + 1
            """
        )
        run_cell("y = 42")
        run_cell("logging.info(z)")
        assert_not_detected("`z` does not depend on `y`")
        run_cell("logging.info(y)")
        assert_not_detected("`y` is updated")
        run_cell("logging.info(x)")
        assert_detected("`x` depends on old value of `y`")
        run_cell("logging.info(a)")
        assert_detected("`a` depends on old value of `y`")

    def test_walrus_fancy_attributes():
        run_cell(
            """
            class Foo:
                def __init__(self, x):
                    self.x = x
            """
        )
        run_cell("foo = Foo(9001)")
        run_cell(
            """
            if (x := (y := (z := 1) + foo.x) + 1) > 0:
                a = x + 1
            """
        )
        run_cell("foo.x = 42")
        run_cell("logging.info(z)")
        assert_not_detected("`z` does not depend on `foo.x`")
        run_cell("logging.info(y)")
        assert_detected("`y` depends on old `foo.x`")
        run_cell("logging.info(x)")
        assert_detected("`x` depends on old value of `foo.x`")
        run_cell("logging.info(a)")
        assert_detected("`a` depends on old value of `foo.x`")
