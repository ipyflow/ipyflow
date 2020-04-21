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


def assert_bool(val, msg=''):
    assert val, str(msg)


def _detected():
    return SAFETY_STATE.test_and_clear_detected_flag()


def assert_detected(msg=''):
    assert_bool(_detected(), msg=msg)


def assert_false_positive(msg=''):
    """
    Same as `assert_detected` but asserts a false positive.
    Helps with searchability of false positives in case we want to fix these later.
    """
    return assert_detected(msg=msg)


def assert_not_detected(msg=''):
    assert_bool(not _detected(), msg=msg)


def assert_false_negative(msg=''):
    """
    Same as `assert_not_detected` but asserts a false negative.
    Helps with searchability of false negatives in case we want to fix these later.
    """
    return assert_not_detected(msg=msg)


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
    assert_false_positive('y depends on stale lst[0]')


def test_subscript_dependency_fp():
    run_cell('lst = [0, 1, 2]')
    run_cell('x = 5')
    run_cell('y = x + lst[0]')
    run_cell('lst[1] = 10')
    run_cell('logging.info(y)')
    assert_false_positive('false positive on unchanged lst[0] but OK since fine-grained detection hard')


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
for ret in map(lambda x: x * 5, retvals):
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
def test_func_assign_helper_func_2():
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


@pytest.mark.skipif(**should_skip_known_failing())
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


@pytest.mark.skipif(**should_skip_known_failing())
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


@pytest.mark.skipif(**should_skip_known_failing())
def test_attributes_2():
    run_cell("""
class Foo(object):
    def __init__(self, x):
        self.x = x
""")
    run_cell('x = Foo(5)')
    run_cell('y = x.x + 5')
    run_cell('x = 8')
    run_cell('logging.info(y)')
    assert_detected('y depends on stale x')


@pytest.mark.skipif(**should_skip_known_failing())
def test_numpy_subscripting_fp():
    run_cell('import numpy as np')
    run_cell('x = np.zeros(5)')
    run_cell('y = x[3] + 5')
    run_cell('x[3] = 2')
    run_cell('logging.info(y)')
    assert_detected('y depends on stale x[3]')


@pytest.mark.skipif(**should_skip_known_failing())
def test_numpy_subscripting_fp():
    run_cell('import numpy as np')
    run_cell('x = np.zeros(5)')
    run_cell('y = x[3] + 5')
    run_cell('x[0] = 2')
    run_cell('logging.info(y)')
    assert_false_positive('false positive on changed x[3] but OK since fine-grained detection hard')


def test_old_format_string():
    run_cell('a = 5; b = 7')
    run_cell('expr_str = "{} + {} = {}".format(a, b, a + b)')
    run_cell('a = 9')
    run_cell('logging.info(expr_str)')
    assert_detected('`expr_str` depends on stale `a`')


@pytest.mark.skipif(**should_skip_known_failing())
def test_old_format_string_kwargs():
    run_cell('a = 5; b = 7')
    run_cell('expr_str = "{a} + {b} = {total}".format(a=a, b=b, total=a + b)')
    run_cell('a = 9')
    run_cell('logging.info(expr_str)')
    assert_detected('`expr_str` depends on stale `a`')


@pytest.mark.skipif(**should_skip_known_failing())
def test_new_format_string():
    run_cell('a = 5; b = 7')
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


@pytest.mark.skipif(**should_skip_known_failing())
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


@pytest.mark.skipif(**should_skip_known_failing())
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


@pytest.mark.skipif(**should_skip_known_failing())
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


@pytest.mark.skipif(**should_skip_known_failing())
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
