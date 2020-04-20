"""
Use "ipython test_dependencies.py" command to run these tests.

Although py.test should also work fine, the main project has to be run in an IPython
environment, without which many functions will complain. Importing things could
solve this problem, but I decided to implement this using ipytest since it is
also something from IPython.
"""
import logging
import os

from IPython import get_ipython
import pytest

from nbsafety.safety import DependencySafety


logging.basicConfig(level=logging.ERROR)
SAFETY_STATE = None


def assert_detected(msg=''):
    assert SAFETY_STATE.test_and_clear_detected_flag(), str(msg)


def assert_not_detected(msg=''):
    assert not SAFETY_STATE.test_and_clear_detected_flag(), str(msg)


def run_cell(code):
    get_ipython().run_cell_magic(SAFETY_STATE.cell_magic_name, None, code)


def should_skip_known_failing(reason='test for unimpled functionality'):
    return {
        'condition': os.environ.get('SHOULD_SKIP_KNOWN_FAILING', True),
        'reason': reason
    }


# Reset dependency graph before each test to prevent unexpected stale dependency
@pytest.fixture(autouse=True)
def init_or_reset_dependency_graph():
    global SAFETY_STATE
    SAFETY_STATE = DependencySafety()
    run_cell('import logging')


def test_subscript_dependency():
    run_cell('lst = [0, 1, 2]')
    run_cell('x = 5')
    run_cell('y = x + lst[0]')
    run_cell('lst[0] = 10')
    run_cell('logging.info(y)')
    assert_detected("Did not detect that lst changed underneath y")


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


# Foo, bar example from the project prompt
def test_foo_bar_example():
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


def test_variable_scope2():
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


def test_default_args():
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
    assert_detected("Should have detected stale dependency of fn foo() on x")


@pytest.mark.skipif(**should_skip_known_failing())
def test_same_pointer():
    # a and b are actually pointing to the same thing
    run_cell('a = [7]')
    run_cell('b = a')
    run_cell('c = b + [5]')

    run_cell('a[0] = 8')
    run_cell('logging.info(b)')
    assert_not_detected("b is an alias of a, updating a should automatically update b as well")
    run_cell('logging.info(c)')
    assert_detected("c does not point to the same thing as a or b, thus there is a stale dependency here ")


def test_func_assign():
    run_cell("""
a = 1
b = 1
c = 2
d = 3
def func(x, y = a):
    e = c+d
    f = x + y
    return f
""")
    run_cell("""
z = func(c)""")
    run_cell("""
a = 4""")
    run_cell("""
logging.info(z)""")
    assert_detected("Should have detected stale dependency of fn func on a")
    run_cell("""
def func(x, y = a):
    logging.info(b)
    e = c+d
    f = x + y
    return f
z = func(c)
""")
    run_cell("""
logging.info(z)""")
    assert_not_detected()
    run_cell("""
c = 3""")
    run_cell("""
logging.info(z)""")
    assert_detected("Should have detected stale dependency of z on c")
    run_cell("""
z = func(c)""")
    run_cell("""
logging.info(z)""")
    assert_not_detected()
    run_cell("""
b = 4""")
    run_cell("""
d = 1""")
    assert_not_detected("Changing b and d should not affect z")


def test_func_assign_helper_func():
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
    assert_detected("Should have detected stale dependency of y on x")
    run_cell('y = f()')
    run_cell('logging.info(y)')
    assert_not_detected()
    run_cell('a = 1')
    run_cell('logging.info(y)')
    assert_not_detected("Changing a should not affect y")


@pytest.mark.skipif(**should_skip_known_failing())
def test_func_assign_helper_func2():
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
    assert_detected("Should have detected stale dependency of y on x")
