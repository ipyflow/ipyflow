# -*- coding: utf-8 -*-
import logging

import pytest

from .utils import make_safety_fixture, skipif_known_failing


logging.basicConfig(level=logging.ERROR)


# Reset dependency graph before each test
_safety_fixture, _safety_state, run_cell_ = make_safety_fixture()


def run_cell(cell):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell)


def stale_detected():
    return _safety_state[0].test_and_clear_detected_flag()


def assert_bool(val, msg=''):
    assert val, str(msg)


def assert_detected(msg=''):
    assert_bool(stale_detected(), msg=msg)


def assert_detected_if_full_propagation(msg=''):
    if _safety_state[0].no_stale_propagation_for_same_cell_definition:
        assert_not_detected(msg=msg)
    else:
        assert_detected(msg=msg)


def assert_false_positive(msg=''):
    """
    Same as `assert_detected` but asserts a false positive.
    Helps with searchability of false positives in case we want to fix these later.
    """
    return assert_detected(msg=msg)


def assert_not_detected(msg=''):
    assert_bool(not stale_detected(), msg=msg)


def assert_false_negative(msg=''):
    """
    Same as `assert_not_detected` but asserts a false negative.
    Helps with searchability of false negatives in case we want to fix these later.
    """
    return assert_not_detected(msg=msg)


def test_simplest():
    run_cell('a = 1')
    run_cell('b = a + 1')
    run_cell('a = 3')
    run_cell('logging.info(b)')
    assert_detected('should have detected b has stale dep on old a')


def test_subscript_dependency():
    run_cell('lst = [0, 1, 2]')
    run_cell('x = 5')
    run_cell('y = x + lst[0]')
    run_cell('lst[0] = 10')
    run_cell('logging.info(y)')
    assert_detected('`y` depends on stale `lst[0]`')


def test_long_chain():
    run_cell('a = 1')
    run_cell('b = a + 1')
    run_cell('c = b + 1')
    run_cell('d = c + 1')
    run_cell('e = d + 1')
    run_cell('f = e + 1')
    assert_not_detected('everything OK so far')
    run_cell('a = 2')
    run_cell('logging.info(f)')
    assert_detected('f has stale dependency on old value of a')


def test_redef_after_stale_use():
    run_cell('a = 1')
    run_cell('b = a + 1')
    run_cell('a = 3')
    run_cell("""
logging.info(b)
b = 7
""")
    assert_detected('b has stale dependency on old value of a')


def test_subscript_dependency_fp():
    run_cell('lst = [0, 1, 2]')
    run_cell('x = 5')
    run_cell('y = x + lst[0]')
    run_cell('lst[1] = 10')
    run_cell('logging.info(y)')
    assert_not_detected('y depends only on unchanged lst[0] and not on changed lst[1]')


# simple test about the basic assignment
def test_basic_assignment():
    run_cell('a = 1')
    run_cell('b = 2')
    run_cell('c = a+b')
    run_cell('d = c+1')
    run_cell('logging.info(a,b,c,d)')
    # redefine a here but not c and d
    run_cell('a = 7')
    run_cell('logging.info(a,b,c,d)')
    assert_detected("Did not detect that c's reference was changed")

    run_cell('c = a+b')
    run_cell('logging.info(a,b,c,d)')
    assert_detected("Did not detect that d's reference was changed")

    run_cell('d = c+1')
    run_cell('logging.info(a,b,c,d)')
    assert_not_detected("There should be no more dependency issue")


# redefined function example from the project prompt
def test_redefined_function_in_list():
    run_cell("""
def foo():
    return 5

def bar():
    return 7
""")
    run_cell("""
funcs_to_run = [foo,bar]
""")
    run_cell("""
accum = 0
for f in funcs_to_run:
    accum += f()
logging.info(accum)
""")
    
    # redefine foo here but not funcs_to_run
    run_cell("""
def foo():
    return 10

def bar():
    return 7
""")
    run_cell("""
accum = 0
for f in funcs_to_run:
    accum += f()
logging.info(accum)
""")
    assert_detected("Did not detect that funcs_to_run's reference was changed")

    run_cell("""
funcs_to_run = [foo,bar]
""")
    run_cell("""
accum = 0
for f in funcs_to_run:
    accum += f()
logging.info(accum)
""")
    assert_not_detected("There should be no more dependency issue")


# like before but the function is called in a list comprehension
def test_redefined_function_for_funcall_in_list_comp():
    run_cell("""
def foo():
    return 5

def bar():
    return 7
""")
    run_cell('retvals = [foo(), bar()]')
    run_cell("""
accum = 0
for ret in retvals:
    accum += ret
logging.info(accum)
""")

    # redefine foo here but not funcs_to_run
    run_cell("""
def foo():
    return 10

def bar():
    return 7
""")
    run_cell('logging.info(accum)')
    assert_detected('Did not detect stale dependency of `accum` on `foo` and `bar`')


# like before but we run the list through a function before iterating
def test_redefined_function_for_funcall_in_modified_list_comp():
    run_cell("""
def foo():
    return 5

def bar():
    return 7
""")
    run_cell('retvals = tuple([foo(), bar()])')
    run_cell("""
accum = 0
# for ret in map(lambda x: x * 5, retvals):
for ret in retvals:
    accum += ret
logging.info(accum)
""")

    # redefine foo here but not funcs_to_run
    run_cell("""
def foo():
    return 10

def bar():
    return 7
""")
    run_cell('logging.info(accum)')
    assert_detected('Did not detect stale dependency of `accum` on `foo` and `bar`')


@pytest.mark.parametrize("no_stale_propagation_for_same_cell_definition", [True, False])
def test_for_loop_with_map(no_stale_propagation_for_same_cell_definition):
    _safety_state[0].no_stale_propagation_for_same_cell_definition = no_stale_propagation_for_same_cell_definition
    run_cell("""
accum = 0
foo = [1, 2, 3, 4, 5]
for ret in map(lambda x: x * 5, foo):
    accum += ret
logging.info(accum)
""")
    assert_not_detected('no stale dep foo -> accum')
    run_cell('foo = [0]')
    run_cell('logging.info(accum)')
    assert_detected_if_full_propagation('should detect stale dep foo -> accum unless only propagating past cell bounds')


def test_redefined_function_over_list_comp():
    run_cell("""
def foo():
    return 5

def bar():
    return 7

def baz(lst):
    return map(lambda x: 3*x, lst)
""")
    run_cell('retvals = baz([foo(), bar()])')
    run_cell("""
accum = 0
for ret in map(lambda x: x * 5, retvals):
    accum += ret
""")
    run_cell("""
def baz(lst):
    return map(lambda x: 7*x, lst)
""")
    run_cell('logging.info(accum)')
    assert_detected('Did not detect stale dependency of `accum` on `baz`')


# like before but the function is called in a tuple comprehension
def test_redefined_function_for_funcall_in_tuple_comp():
    run_cell("""
def foo():
    return 5

def bar():
    return 7
""")
    run_cell('retvals = (foo(), bar())')
    run_cell("""
accum = 0
for ret in retvals:
    accum += ret
logging.info(accum)
""")

    # redefine foo here but not funcs_to_run
    run_cell("""
def foo():
    return 10

def bar():
    return 7
""")
    run_cell('logging.info(accum)')
    assert_detected('Did not detect stale dependency of `accum` on `foo` and `bar`')


# Tests about variables that have same name but in different scope.
# There shouldn't be any extra dependency because of the name.
def test_variable_scope():
    run_cell("""
def func():
    x = 6
""")
    run_cell('x = 7')
    run_cell('y = x')
    run_cell('z = func')
    run_cell('logging.info(y,z())')

    # change x inside of the function, but not x outside of the function
    run_cell('def func():\n    x = 10')
    run_cell('logging.info(y,z())')
    assert_detected("Did not detect the dependency change in the function")

    run_cell('y = x')
    run_cell('logging.info(y,z())')
    assert_detected("Updating y should not solve the dependency change inside of function func")

    run_cell('z = func')
    run_cell('logging.info(y,z())')
    assert_not_detected("Updating z should solve the problem")


def test_variable_scope_2():
    run_cell('def func():\n    x = 6')
    run_cell('x = 7')
    run_cell('y = x')
    run_cell('z = func')
    run_cell('logging.info(y,z())')

    # change x outside of the function, but not inside of the function
    run_cell('x = 10')
    run_cell('logging.info(y,z())')
    assert_detected("Did not detect the dependency change outside of the function")

    run_cell('z = func')
    run_cell('logging.info(y,z())')
    assert_detected("Updating z should not solve the dependency change outside of function")

    run_cell('y = x')
    run_cell('logging.info(y,z())')
    assert_not_detected("Updating y should solve the problem")


@pytest.mark.parametrize("no_stale_propagation_for_same_cell_definition", [True, False])
def test_default_args(no_stale_propagation_for_same_cell_definition):
    _safety_state[0].no_stale_propagation_for_same_cell_definition = no_stale_propagation_for_same_cell_definition
    run_cell("""
x = 7
def foo(y=x):
    return y + 5
""")
    run_cell('a = foo()')
    assert_not_detected()
    run_cell('x = 10')
    assert_not_detected()
    run_cell('b = foo()')
    assert_detected_if_full_propagation("Should have detected stale dependency of fn foo() on x")


def test_same_pointer():
    # a and b are actually pointing to the same thing
    run_cell('a = [7]')
    run_cell('b = a')
    run_cell('c = b + [5]')

    run_cell('a[0] = 8')
    run_cell('logging.info(b)')
    assert_not_detected('`b` is an alias of `a`, updating a should automatically update `b` as well')
    run_cell('logging.info(c)')
    assert_detected('`c` does not point to the same thing as `a` or `b`, thus there is a stale dependency here')


def test_func_assign_objs():
    run_cell("""
a = [1]
b = [1]
c = [2]
d = [3]
def func(x, y=a):
    e = [c[0] + d[0]]
    f = [x[0] + y[0]]
    return f
""")
    run_cell('z = func(c)')
    run_cell('a = [4]')
    run_cell('logging.info(z[0])')
    assert_detected("Should have detected stale dependency of fn func on a")
    run_cell("""
def func(x, y=a):
    logging.info(b[0])
    e = [c[0] + d[0]]
    f = [x[0] + y[0]]
    return f
z = func(c)
""")
    run_cell('logging.info(z[0])')
    assert_not_detected()
    run_cell('c = [3]')
    run_cell('logging.info(z[0])')
    assert_detected("Should have detected stale dependency of z on c")
    run_cell('z = func(c)')
    run_cell('logging.info(z[0])')
    assert_not_detected()
    run_cell('b = [4]')
    run_cell('d = [1]')
    assert_not_detected("Changing b and d should not affect z")


def test_func_assign_ints():
    run_cell("""
a = 1
b = 1
c = 2
d = 3
def func(x, y=a):
    e = c + d
    f = x + y
    return f
""")
    run_cell('z = func(c)')
    run_cell('a = 4')
    run_cell('logging.info(z)')
    assert_detected("Should have detected stale dependency of fn func on a")
    run_cell("""
def func(x, y=a):
    logging.info(b)
    e = c + d
    f = x + y
    return f
z = func(c)
""")
    run_cell('logging.info(z)')
    assert_not_detected()
    run_cell('c = 3')
    run_cell('logging.info(z)')
    assert_detected("Should have detected stale dependency of z on c")
    run_cell('z = func(c)')
    run_cell('logging.info(z)')
    assert_not_detected()
    run_cell('b = 4')
    run_cell('d = 1')
    assert_not_detected("Changing b and d should not affect z")


@pytest.mark.parametrize("no_stale_propagation_for_same_cell_definition", [True, False])
def test_func_assign_helper_func(no_stale_propagation_for_same_cell_definition):
    _safety_state[0].no_stale_propagation_for_same_cell_definition = no_stale_propagation_for_same_cell_definition
    run_cell("""
x = 3
a = 4
def f():
    def g():
        logging.info(a)
        return x
    return g()
y = f()
""")
    run_cell('x = 4')
    run_cell('logging.info(y)')
    assert_detected_if_full_propagation("Should have detected stale dependency of y on x")
    run_cell('y = f()')
    run_cell('logging.info(y)')
    assert_not_detected()
    run_cell('a = 1')
    run_cell('logging.info(y)')
    assert_not_detected("Changing a should not affect y")


@pytest.mark.parametrize("no_stale_propagation_for_same_cell_definition", [True, False])
def test_func_assign_helper_func_2(no_stale_propagation_for_same_cell_definition):
    _safety_state[0].no_stale_propagation_for_same_cell_definition = no_stale_propagation_for_same_cell_definition
    run_cell("""
x = 3
a = 4
def f():
    def g():
        logging.info(a)
        return x
    return g
y = f()()
""")
    run_cell('x = 4')
    run_cell('logging.info(y)')
    assert_detected_if_full_propagation("Should have detected stale dependency of y on x")


def test_branching():
    run_cell('y = 7')
    run_cell('x = y + 3')
    run_cell("""
if True:
    b = 5
else:
    y = 7
""")
    run_cell('logging.info(x)')
    assert_not_detected('false positive on unchanged y')


def test_branching_2():
    run_cell('y = 7')
    run_cell('x = y + 3')
    run_cell("""
if False:
    b = 5
else:
    y = 9
""")
    run_cell('logging.info(x)')
    assert_detected('x depends on stale y')


def test_identity_checking():
    run_cell('y = 7')
    run_cell('x = y + 3')
    run_cell('y = 7')
    run_cell('logging.info(x)')
    assert_not_detected('`y` was not mutated')


@skipif_known_failing
def test_identity_checking_obj():
    # To get this working properly, we need to create datasyms for all of the values in the literal
    run_cell('y = [7]')
    run_cell('x = y + [3]')
    run_cell('y[0] = 7')
    run_cell('logging.info(x)')
    assert_not_detected('`y` was not mutated')


def test_attributes():
    run_cell("""
class Foo(object):
    def __init__(self, x):
        self.x = x
""")
    run_cell('x = Foo(5)')
    run_cell('y = x.x + 5')
    run_cell('x.x = 8')
    run_cell('logging.info(y)')
    assert_detected('y depends on stale attrval x.x')


def test_attributes_2():
    run_cell("""
class Foo(object):
    def __init__(self, x):
        self.x = x
""")
    run_cell('x = Foo(5)')
    run_cell('y = x.x + 5')
    run_cell('%safety show_deps x')
    run_cell('%safety show_deps y')
    run_cell('x = 8')
    run_cell('logging.info(y)')
    assert_detected('y depends on stale x')


def test_attributes_3():
    run_cell("""
class Foo(object):
    def __init__(self, x, y):
        self.x = x
        self.y = y
""")
    run_cell('foo = Foo(5, 6)')
    run_cell('bar = Foo(7, 8)')
    run_cell('y = bar.x + 5')
    run_cell('foo.x = 8')
    run_cell('logging.info(y)')
    assert_not_detected('y does not depend on updated attrval foo.y')


def test_stale_use_of_attribute():
    run_cell("""
class Foo(object):
    def __init__(self, x, y):
        self.x = x
        self.y = y
""")
    run_cell('foo = Foo(5, 6)')
    run_cell('bar = Foo(7, 8)')
    run_cell('foo.x = bar.x + bar.y')
    run_cell('bar.y = 42')
    run_cell('logging.info(foo.x)')
    assert_detected('`foo.x` depends on stale `bar.y`')


def test_attr_manager_active_scope_resets():
    run_cell("""
y = 10
class Foo(object):
    def f(self):
        y = 11
        return y
def f():
    return y
""")
    run_cell('foo = Foo()')
    # if the active scope doesn't reset after done with foo.f(),
    # it will think the `y` referred to by f() is the one in Foo.f's scope.
    run_cell('x = foo.f() + f()')
    run_cell('y = 42')
    run_cell('logging.info(x)')
    assert_detected('`x` depends on stale `y`')


def test_attribute_call_point():
    run_cell("""
y = 10
class Foo(object):
    def f(self):
        return y
""")
    run_cell('foo = Foo()')
    run_cell('x = foo.f()')
    run_cell('y = 42')
    run_cell('logging.info(x)')
    assert_detected('`x` depends on stale `y`')


def test_attr_manager_active_scope_with_property():
    run_cell("""
y = 10
class Foo(object):
    @property
    def f(self):
        y = 11
        return y
""")
    run_cell('foo = Foo()')
    # if the active scope doesn't reset after done with foo.f(),
    # it will think the `y` referred to by f() is the one in Foo.f's scope.
    run_cell('x = foo.f')
    run_cell('y = 42')
    run_cell('logging.info(x)')
    assert_not_detected('`x` independent of outer `y`')


def test_namespace_scope_resolution():
    run_cell("""
y = 42
class Foo(object):
    y = 10
    @property
    def foo(self):
        return y
""")
    run_cell('foo = Foo()')
    run_cell('x = foo.foo')
    run_cell('Foo.y = 99')
    run_cell('logging.info(x)')
    assert_not_detected('`x` should not have dependency on `Foo.y`')


def test_long_chain_attribute():
    run_cell("""
class Foo(object):
    shared = 99
    def __init__(self, x, y):
        self.x = x
        self.y = y + self.shared
        
    class Bar(object):
        def __init__(self, a, b):
            self.a = a
            self.b = b
            
        def foo(self):
            return Foo(self.a, self.b)
            
        def bar(self):
            return Foo
""")
    run_cell('foo = Foo(5, 6)')
    run_cell('bar = Foo.Bar(7, 8)')
    run_cell('foo.x = 42')
    run_cell('logging.info(bar.a)')
    assert_not_detected()
    run_cell('Foo.Bar(9, 10).foo().shared = 100')
    run_cell("""
for _ in range(10**2):
    Foo.Bar(9, 10).foo().shared = 100
""")
    run_cell('logging.info(foo.y)')
    assert_not_detected('we mutated `shared` on obj and not on `Foo` (the one on which `foo.y` depends)')
    run_cell('Foo.Bar(9, 10).bar().shared = 100')
    run_cell('logging.info(foo.y)')
    assert_detected('we mutated a shared value on which `foo.y` depends')


def test_numpy_subscripting():
    run_cell('import numpy as np')
    run_cell('x = np.zeros(5)')
    run_cell('y = x[3] + 5')
    run_cell('x[3] = 2')
    run_cell('logging.info(y)')
    assert_detected('y depends on stale x[3]')


def test_dict_subscripting():
    run_cell('d = {"foo": "bar", 0: "bat"}')
    run_cell('x = 7')
    run_cell('d[0] = x')
    run_cell('x += 1')
    run_cell('z = d["foo"] + " asdf"')
    assert_not_detected('`d["foo"]` does not depend on stale `x`, unlike `d[0]`')
    run_cell('logging.info(z)')
    assert_not_detected()
    run_cell('logging.info(d["foo"])')
    assert_not_detected()


def test_dict_subscripting_2():
    run_cell('d = {"foo": "bar", 0: "bat"}')
    run_cell('x = 7')
    run_cell('d[0] = x')
    run_cell('x += 1')
    run_cell('d[0] = x')
    run_cell('logging.info(d)')
    assert_not_detected('we updated the stale entry of `d`')


def test_pandas_attr_mutation_with_alias():
    run_cell('import pandas as pd')
    run_cell('df = pd.DataFrame({"a": [0,1], "b": [2., 3.]})')
    run_cell('asdf = df.b')
    run_cell('df.b *= 7')
    run_cell('logging.info(asdf)')
    assert_detected('`asdf` has same values as `df.b`, but they now point to different things, '
                    'which in general could be dangerous')


def test_list_alias_breaking():
    run_cell('x = [0]')
    run_cell('y = x')
    run_cell('x = [7]')
    run_cell('logging.info(y)')
    assert_detected('`y` has stale dependency on old `x`')


def test_list_alias_no_break():
    run_cell('x = [0]')
    run_cell('y = x')
    run_cell('x *= 7')
    run_cell('logging.info(y)')
    assert_not_detected('`y` still aliases `x`')


@skipif_known_failing
def test_subscript_sensitivity():
    run_cell('lst = list(range(5))')
    run_cell('i = 0')
    run_cell('lst[i] = 10')
    run_cell('i = 1')
    run_cell('logging.info(lst)')
    assert_detected('lst depends on stale i')


def test_subscript_adds():
    run_cell('x = 42')
    run_cell("""
d = {
    'foo': x,
    'bar': 77
}
""")
    run_cell('d["bar"] = 99')
    run_cell('x = 100')
    run_cell('logging.info(d)')
    assert_detected('`d` depends on stale `x`')


def test_list_mutation():
    run_cell('lst = list(range(5))')
    run_cell('x = 42')
    run_cell('asdf = []')
    run_cell('asdf.append(lst.append(x))')
    run_cell('x = 43')
    run_cell('logging.info(lst)')
    assert_detected('lst depends on stale x')


def test_list_mutation_2():
    run_cell('lst = list(range(5))')
    run_cell('x = lst + [42, 43]')
    run_cell('lst.append(99)')
    run_cell('logging.info(x)')
    assert_detected('`x` depends on stale `lst`')


def test_lazy_class_scope_resolution():
    run_cell("""
class Foo(object):
    shared = 99
    def __init__(self, x):
        self.x = x
""")
    run_cell('foo = Foo(10)')
    run_cell('y = 11')
    run_cell('Foo.shared = y + 42')
    run_cell('y = 12')
    run_cell('logging.info(foo.shared)')
    assert_detected('`foo.shared` should point to same DataSymbol as `Foo.shared` and thus also has stale dep')
    run_cell('foo.shared = 89')
    run_cell('logging.info(Foo.shared)')
    assert_detected('Even though we refreshed `foo.shared`, this '
                    'has no bearing on the original class member `Foo.shared`')


def test_new_scope_val_depends_on_old():
    run_cell("""
class Foo(object):
    shared = 99
""")
    run_cell('foo = Foo()')
    run_cell('foo.shared = 11')
    run_cell('foo_shared_alias = foo.shared')
    run_cell('Foo.shared = 12')
    run_cell('logging.info(foo_shared_alias)')
    assert_detected()
    run_cell('logging.info(foo.shared)')
    assert_detected()
    run_cell('logging.info(foo)')
    assert_detected()


def test_class_member_mutation_does_not_affect_instance_members():
    run_cell("""
class Foo(object):
    shared = 99
    def __init__(self):
        self.x = 42
""")
    run_cell('foo = Foo()')
    run_cell('Foo.shared = 12')
    run_cell('logging.info(foo.x)')
    assert_not_detected()


def test_numpy_subscripting_fp():
    run_cell('import numpy as np')
    run_cell('x = np.zeros(5)')
    run_cell('y = x[3] + 5')
    run_cell('x[0] = 2')
    run_cell('logging.info(y)')
    assert_not_detected('`y` depends on unchanged `x[3]` and not on changed `x[0]`')


def test_old_format_string():
    run_cell('a = 5\nb = 7')
    run_cell('expr_str = "{} + {} = {}".format(a, b, a + b)')
    run_cell('a = 9')
    run_cell('logging.info(expr_str)')
    assert_detected('`expr_str` depends on stale `a`')


def test_old_format_string_kwargs():
    run_cell('a = 5\nb = 7')
    run_cell('expr_str = "{a} + {b} = {total}".format(a=a, b=b, total=a + b)')
    run_cell('a = 9')
    run_cell('logging.info(expr_str)')
    assert_detected('`expr_str` depends on stale `a`')


def test_new_format_string():
    run_cell('a = 5\nb = 7')
    run_cell('expr_str = f"{a} + {b} = {a+b}"')
    run_cell('a = 9')
    run_cell('logging.info(expr_str)')
    assert_detected('`expr_str` depends on stale `a`')


def test_scope_resolution():
    run_cell("""
def f(x):
    def g(x):
        return 2 * x
    return g(x) + 8
""")
    run_cell('x = 7')
    run_cell('y = f(x)')
    run_cell('x = 8')
    run_cell('logging.info(y)')
    assert_detected('`y` depends on stale `x`')


def test_scope_resolution_2():
    run_cell("""
def g(x):
    return 2 * x
def f(x):
    return g(x) + 8
""")
    run_cell('x = 7')
    run_cell('y = f(x)')
    run_cell('x = 8')
    run_cell('logging.info(y)')
    assert_detected('`y` depends on stale `x`')


def test_funcall_kwarg():
    run_cell("""
def f(y):
    return 2 * y + 8
""")
    run_cell('x = 7')
    run_cell('z = f(y=x)')
    run_cell('x = 8')
    run_cell('logging.info(z)')
    assert_detected('`z` depends on stale `x`')


def test_funcall_kwarg_2():
    run_cell("""
def f(y):
    return 2 * y + 8
""")
    run_cell('x = 7')
    run_cell('y = f(y=x)')
    run_cell('x = 8')
    run_cell('logging.info(y)')
    assert_detected('`y` depends on stale `x`')


def test_funcall_kwarg_3():
    run_cell("""
def f(x):
    return 2 * x + 8
""")
    run_cell('x = 7')
    run_cell('y = f(x=x)')
    run_cell('x = 8')
    run_cell('logging.info(y)')
    assert_detected('`y` depends on stale `x`')


def test_funcall_kwarg_4():
    run_cell("""
def f(x):
    return 2 * x + 8
""")
    run_cell('x = 7')
    run_cell('x = f(x=x)')
    run_cell('x = 8')
    run_cell('logging.info(x)')
    assert_not_detected('`x` is overriden so should not be stale')


def test_single_line_dictionary_literal():
    run_cell('foo = 5')
    run_cell('bar = 6')
    run_cell('d = {foo: bar, "pi": 42,}')
    run_cell('bar = 7')
    run_cell('logging.info(d)')
    assert_detected('`d` depends on stale `bar`')


def test_single_line_dictionary_literal_fix_stale_deps():
    run_cell('foo = 5')
    run_cell('bar = 6')
    run_cell('d = {foo: bar, "pi": 42,}')
    run_cell('bar = 7')
    run_cell('logging.info(d)')
    assert_detected('`d` depends on stale `bar`')
    run_cell('d[foo] = bar')
    run_cell('logging.info(d)')
    assert_false_positive('`d`s stale dep fixed, but this is hard to detect '
                          'since we did not yet have a DataSymbol for `d[foo]` when staleness introduced')
    run_cell('foo = 8')
    run_cell('logging.info(d)')
    assert_detected('`d` depends on stale `foo`')
    # TODO: not sure what the correct behavior for the below write to d[foo] after foo changed should be
    # run_cell('d[foo] = bar')
    # run_cell('logging.info(d)')
    # assert_not_detected('`d`s stale dep fixed')


def test_multiline_dictionary_literal():
    run_cell('foo = 5')
    run_cell('bar = 6')
    run_cell("""
d = {
    foo: bar,
    'pi': 42,
}
""")
    run_cell('bar = 7')
    run_cell('logging.info(d)')
    assert_detected('`d` depends on stale `bar`')


def test_exception():
    run_cell('lst = list(range(5))')
    run_cell('x = 6')
    run_cell("""
try:
    lst[x] = 42
except:
    lst[0] = 42
""")
    run_cell('x = 7')
    run_cell('logging.info(lst)')
    assert_not_detected('lst should be independent of x due to exception')


def test_for_loop_binding():
    run_cell('a = 0')
    run_cell('b = 1')
    run_cell('c = 2')
    run_cell('lst = [a, b, c]')
    run_cell("""
for i in lst:
    pass
""")
    run_cell('a = 3')
    run_cell('logging.info(i)')
    assert_false_positive('`i` should not depend on `a` at end of for loop but this is hard')


@skipif_known_failing
def test_for_loop_literal_binding():
    run_cell('a = 0')
    run_cell('b = 1')
    run_cell('c = 2')
    run_cell("""
for i in [a, b, c]:
    pass
""")
    run_cell('a = 3')
    run_cell('logging.info(i)')
    assert_not_detected('`i` should not depend on `a` at end of for loop')


def test_same_cell_redefine():
    run_cell('a = 0')
    run_cell("""
b = a + 1
a = 42
""")
    run_cell('logging.info(b)')
    assert_not_detected('`b` should not be considered as having stale dependency since `a` changed in same cell as `b`')


@skipif_known_failing
def test_multiple_stmts_in_one_line():
    run_cell('a = 1; b = 2')
    run_cell('x = a + b')
    run_cell('a = 42')
    run_cell('logging.info(x)')
    assert_detected('`x` depends on stale value of `a`')


def test_multiple_stmts_in_one_line_2():
    run_cell('a = 1; b = 2')
    run_cell('x = a + b')
    run_cell('b = 42')
    run_cell('logging.info(x)')
    assert_detected('`x` depends on stale value of `a`')


def test_line_magic():
    run_cell("""
%lsmagic
%lsmagic
%lsmagic
%lsmagic
%lsmagic
a = 0
""")
    run_cell("""
%lsmagic
%lsmagic
%lsmagic
%lsmagic
x = a + 1
""")
    run_cell("""
%lsmagic
%lsmagic
%lsmagic
a = 42
""")
    run_cell("""
%lsmagic
%lsmagic
logging.info(x)
""")
    assert_detected('`x` depends on stale value of `a`')
    run_cell("""
%lsmagic
logging.info(x)
%lsmagic
""")
    assert_detected('`x` depends on stale value of `a`')


def test_exception_stack_unwind():
    import builtins
    safety_state = '_safety_state'
    test_passed = 'test_passed'
    setattr(builtins, safety_state, _safety_state[0])
    setattr(builtins, test_passed, True)
    test_passed = "'" + test_passed + "'"

    def assert_stack_size(size):
        return ';'.join([
            f'can_pass = len({safety_state}.trace_state.stack) == {size} '
            f'and len({safety_state}.attr_trace_manager.stack) == {size}',
            f'setattr(builtins, {test_passed}, getattr(builtins, {test_passed}) and can_pass)'
        ])
    try:
        run_cell(f"""
import builtins
import numpy as np
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
""")
    finally:
        delattr(builtins, safety_state)
    test_passed = test_passed.strip("'")
    try:
        assert getattr(builtins, test_passed), 'unexpected stack size somewhere'
    finally:
        delattr(builtins, test_passed)


def test_throwing_statements_do_not_track_deps():
    run_cell("""
z = 10
def foo():
    def bar():
        raise ValueError('foo!')
    return bar() + z
x = 0
y = x + 1
""")
    run_cell("""
try:
    x = 42 + foo()
except:
    pass
""")
    run_cell('logging.info(y)')
    assert_not_detected('no stale dep for `y` because update on `x` threw exception')
    run_cell('z = 99')
    run_cell('logging.info(x)')
    assert_not_detected('no stale dep for `x` because it is indep of `z` (attempted dep add threw)')


def test_attr_dep_from_somewhere_else():
    run_cell('import sys')
    run_cell('sys.path.append("./test")')
    run_cell('import fake')
    run_cell('fake.y = 7')
    run_cell('x = fake.y + 1')
    run_cell('fake.y = 42')
    run_cell('logging.info(x)')
    assert_detected('`x` depends on old value of `fake.y`')


def test_attr_use_from_somewhere_else():
    run_cell('import sys')
    run_cell('sys.path.append("./test")')
    run_cell('import fake')
    run_cell('x = 7')
    run_cell('fake.y = x + 1')
    run_cell('x = 42')
    run_cell('logging.info(fake.y)')
    assert_detected('`fake.y` depends on old value of `x`')


def test_class_assignment():
    run_cell("""
class Foo:
    def __init__(self):
        self.y = 99

Bar = Foo
foo = Bar()
x = 7
""")
    run_cell('foo.y = x + 1')
    run_cell('x = 42')
    run_cell('logging.info(foo.y)')
    assert_detected('`foo.y` depends on stale `x`')
    run_cell('foo.y = 10')
    run_cell('x = foo.y + 1')
    run_cell('foo.y = 12')
    run_cell('logging.info(x)')
    assert_detected('`x` depends on stale `foo.y`')


def test_no_class_false_positives():
    run_cell('x = 7')
    run_cell('y = x + 1')
    run_cell('x = 42')
    run_cell("""
try:
    class Foo:
        print(y)
except:
    pass
""")
    assert_not_detected('x inside class scope is different')


def test_tuple_unpack_simple():
    run_cell('x, y = 0, 1')
    run_cell('a, b = x + 2, y + 3')
    run_cell('x, y = 42, 43')
    run_cell('logging.info(a)')
    assert_detected('`a` depends on stale `x`')
    run_cell('logging.info(b)')
    assert_detected('`b` depends on stale `y`')


@skipif_known_failing
def test_tuple_unpack_hard():
    run_cell('x, y = 0, 1')
    run_cell('a, b = x + 2, y + 3')
    run_cell('y = 43')
    run_cell('logging.info(a)')
    assert_not_detected('`a` does not depend on `y`')
    run_cell('logging.info(b)')
    assert_detected('`b` depends on stale `y`')
    run_cell('b = y + 10')
    run_cell('x = 99')
    run_cell('logging.info(b)')
    assert_not_detected('`b` does not depend on `x`')
    run_cell('logging.info(a)')
    assert_detected('`a` depends on stale `x`')


@pytest.mark.parametrize("no_stale_propagation_for_same_cell_definition", [True, False])
def test_attr_dep_with_top_level_overwrite(no_stale_propagation_for_same_cell_definition):
    _safety_state[0].no_stale_propagation_for_same_cell_definition = no_stale_propagation_for_same_cell_definition
    run_cell("""
class Foo:
    def __init__(self):
        self.y = 99
foo = Foo()
x = 42
foo.y = x + 7
""")
    run_cell('x = 43')
    run_cell('logging.info(foo)')  # this should be a 'deep' usage of foo
    assert_detected_if_full_propagation('logging.info could display `foo.y` which depends on x')
    run_cell('foo.y = 70')
    assert_not_detected('we just fixed stale dep of `foo.y` by changing its deps')
    run_cell('x = foo.y + 7')
    run_cell('foo = 81')
    run_cell('logging.info(x)')
    assert_detected('`x` has stale dep on `foo` (transitively through `foo.y`)')


def test_typed_assignment():
    run_cell('a = 0')
    run_cell('b: int = a + 1')
    run_cell('a: int = 42')
    run_cell('logging.info(b)')
    assert_detected('`b` has stale dep on `a`')


@skipif_known_failing
def test_time_line_magic():
    run_cell('a = 0')
    run_cell('b = a + 1')
    run_cell('%time a = 42')
    run_cell('logging.info(b)')
    assert_detected('`b` has stale dep on `a`')


def test_cell_magic():
    run_cell('a = 0')
    run_cell('b = a + 1')
    run_cell('%%time\na = 42')
    run_cell('logging.info(b)')
    assert_detected('`b` has stale dep on `a`')


def test_pandas():
    run_cell('import numpy as np')
    run_cell('import pandas as pd')
    run_cell('arr = 1 + np.arange(10)')
    run_cell('df = pd.DataFrame({"col": arr})')
    run_cell('df2 = df.dropna()')
    run_cell('df.dropna()')
    run_cell('logging.info(df2)')
    assert_not_detected('`df.dropna()` did not mutate `df`')
    run_cell('df.dropna(inplace=True)')
    run_cell('logging.info(df2)')
    assert_detected('`df.dropna(inplace=True)` mutated `df`')


@skipif_known_failing
def test_chain_with_toplevel_assignment():
    # TODO:
    pass
