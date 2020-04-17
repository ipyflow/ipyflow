"""
Use "ipython test_project.py" command to run these tests.

Although py.test should also work fine, but the main project has to be ran in ipython
enviroment, many functions will complain undefined otherwise. Importing things could
solve this problem, but I decided to implement this ipytest since it is also something
from Ipython.
"""
import ipytest

from nbsafety.safety import *

ipytest.config(rewrite_asserts=True, magics=True)
# TODO (smacke): use a proper filter instead of using levels to filter out safety code logging
logging.basicConfig(level=logging.ERROR)


#rewrite the warning from magic cell so that we know it prompts a warning. DETECTED should be set to false again after each time use
original_warning = dependency_safety.warning
DETECTED = False
def better_warning(name,mucn,mark):
    global DETECTED
    DETECTED = True
    original_warning(name,mucn,mark)



def assert_detected(msg = ""):
    global DETECTED
    assert DETECTED, str(msg)
    DETECTED = False

def assert_not_detected(msg = ""):
    assert not DETECTED, str(msg)

#Make sure to seperate each test as a new test to prevent unexpected stale dependency
def new_test():
    dependency_safety_init()
    dependency_safety.warning = better_warning

#The string name of that cell magic function
magic_function = "dependency_safety"

#simple test about the basic assignment
def test_Basic_Assignment_Break():
    new_test()
    get_ipython().run_cell_magic(magic_function, '', 'a = 1')
    get_ipython().run_cell_magic(magic_function, '', 'b = 2')
    get_ipython().run_cell_magic(magic_function, '', 'c = a+b')
    get_ipython().run_cell_magic(magic_function, '', 'd = c+1')
    get_ipython().run_cell_magic(magic_function, '', 'print(a,b,c,d)')
    #redefine a here but not c and d
    get_ipython().run_cell_magic(magic_function, '', 'a = 7')
    get_ipython().run_cell_magic(magic_function, '', 'print(a,b,c,d)')
    assert_detected("Did not detect that c's reference was changed")


    get_ipython().run_cell_magic(magic_function, '', 'c = a+b')
    get_ipython().run_cell_magic(magic_function, '', 'print(a,b,c,d)')
    assert_detected("Did not detect that d's reference was changed")


    get_ipython().run_cell_magic(magic_function, '', 'd = c+1')
    get_ipython().run_cell_magic(magic_function, '', 'print(a,b,c,d)')
    assert_not_detected("There should be no more dependency issue")

#Foo, bar example from the project prompt
def test_Foo_Bar_Example():
    new_test()
    get_ipython().run_cell_magic(magic_function, '', """
def foo():
    return 5

def bar():
    return 7
""")
    get_ipython().run_cell_magic(magic_function, '', """
funcs_to_run = [foo,bar]
""")
    get_ipython().run_cell_magic(magic_function, '', """
accum = 0
for f in funcs_to_run:
    accum += f()
print(accum)
""")
    
    #redefine foo here but not funcs_to_run
    get_ipython().run_cell_magic(magic_function, '', """
def foo():
    return 10

def bar():
    return 7
""")
    get_ipython().run_cell_magic(magic_function, '', """
accum = 0
for f in funcs_to_run:
    accum += f()
print(accum)
""")
    assert_detected("Did not detect that funcs_to_run's reference was changed")


    get_ipython().run_cell_magic(magic_function, '', """
funcs_to_run = [foo,bar]
""")
    get_ipython().run_cell_magic(magic_function, '', """
accum = 0
for f in funcs_to_run:
    accum += f()
print(accum)
""")
    assert_not_detected("There should be no more dependency issue")


#tests about variables that have same name but in different scope. There shouldn't be any extra dependency because of the name
def test_Variable_Scope():
    new_test()
    get_ipython().run_cell_magic(magic_function, '', """
def func():
    x = 6
""")
    get_ipython().run_cell_magic(magic_function, '', 'x = 7')
    get_ipython().run_cell_magic(magic_function, '', 'y = x')
    get_ipython().run_cell_magic(magic_function, '', 'z = func')
    get_ipython().run_cell_magic(magic_function, '', 'print(y,z())')

    #change x inside of the function, but not x outside of the function
    get_ipython().run_cell_magic(magic_function, '', 'def func():\n    x = 10')
    get_ipython().run_cell_magic(magic_function, '', 'print(y,z())')
    assert_detected("Did not detect the dependency change in the function")

    get_ipython().run_cell_magic(magic_function, '', 'y = x')
    get_ipython().run_cell_magic(magic_function, '', 'print(y,z())')
    assert_detected("Updating y should not solve the dependency change inside of function func")

    get_ipython().run_cell_magic(magic_function, '', 'z = func')
    get_ipython().run_cell_magic(magic_function, '', 'print(y,z())')
    assert_not_detected("Updating z should solve the problem")

def test_Variable_Scope2():
    new_test()
    get_ipython().run_cell_magic(magic_function, '', 'def func():\n    x = 6')
    get_ipython().run_cell_magic(magic_function, '', 'x = 7')
    get_ipython().run_cell_magic(magic_function, '', 'y = x')
    get_ipython().run_cell_magic(magic_function, '', 'z = func')
    get_ipython().run_cell_magic(magic_function, '', 'print(y,z())')

    #change x outside of the function, but not inside of the function
    get_ipython().run_cell_magic(magic_function, '', 'x = 10')
    get_ipython().run_cell_magic(magic_function, '', 'print(y,z())')
    assert_detected("Did not detect the dependency change outside of the function")

    get_ipython().run_cell_magic(magic_function, '', 'z = func')
    get_ipython().run_cell_magic(magic_function, '', 'print(y,z())')
    assert_detected("Updating z should not solve the dependency change outside of function")

    get_ipython().run_cell_magic(magic_function, '', 'y = x')
    get_ipython().run_cell_magic(magic_function, '', 'print(y,z())')
    assert_not_detected("Updating y should solve the problem")

def test_default_args():
    new_test()
    get_ipython().run_cell_magic(magic_function, '', """
x = 7
def foo(y=x):
    return y + 5
""")
    get_ipython().run_cell_magic(magic_function, '', 'a = foo()')
    assert_not_detected()
    get_ipython().run_cell_magic(magic_function, '', 'x = 10')
    assert_not_detected()
    get_ipython().run_cell_magic(magic_function, '', 'b = foo()')
    assert_detected("Should have detected stale dependency of fn foo() on x")
    
def test_Same_Pointer():
    new_test()
    #a and b are actually pointing to the same thing
    get_ipython().run_cell_magic(magic_function, '', 'a = [7]')
    get_ipython().run_cell_magic(magic_function, '', 'b = a')
    get_ipython().run_cell_magic(magic_function, '', 'c = b + [5]')

    get_ipython().run_cell_magic(magic_function, '', 'a[0] = 8')
    get_ipython().run_cell_magic(magic_function, '', 'print(b)')
    assert_not_detected("b is an alias of a, updating a should automatically update b as well")
    get_ipython().run_cell_magic(magic_function, '', 'print(c)')
    assert_detected("c does not point to the same thing as a or b, thus there is a stale dependency here ")



def test_func_assign():
    new_test()
    get_ipython().run_cell_magic(magic_function, '', """
a = 1
b = 1
c = 2
d = 3
def func(x, y = a):
    print(b)
    e = c+d
    f = x + y
    return f
""")
    get_ipython().run_cell_magic(magic_function, '', """
z = func(c)""")
    get_ipython().run_cell_magic(magic_function, '', """
a = 4""")
    get_ipython().run_cell_magic(magic_function, '', """
print(z)""")
    assert_detected("Should have detected stale dependency of fn func on a")
    get_ipython().run_cell_magic(magic_function, '', """
def func(x, y = a):
    print(b)
    e = c+d
    f = x + y
    return f
z = func(c)
""")
    get_ipython().run_cell_magic(magic_function, '', """
print(z)""")
    assert_not_detected()
    get_ipython().run_cell_magic(magic_function, '', """
c = 3""")
    get_ipython().run_cell_magic(magic_function, '', """
print(z)""")
    assert_detected("Should have detected stale dependency of z on c")
    get_ipython().run_cell_magic(magic_function, '', """
z = func(c)""")
    get_ipython().run_cell_magic(magic_function, '', """
print(z)""")
    assert_not_detected()
    get_ipython().run_cell_magic(magic_function, '', """
b = 4""")
    get_ipython().run_cell_magic(magic_function, '', """
d = 1""")
    assert_not_detected("Changing b and d should not affect z")

def test_func_assign_herlper_func():
    new_test()
    get_ipython().run_cell_magic(magic_function, '', """
x = 3
a = 4
def f():
    def g():
        print(a)
        return x
    return g()
y = f()
""")
    get_ipython().run_cell_magic(magic_function, '', """
x = 4""")
    get_ipython().run_cell_magic(magic_function, '', """
print(y)""")
    assert_detected("Should have detected stale dependency of y on x")
    get_ipython().run_cell_magic(magic_function, '', """
y = f()""")
    get_ipython().run_cell_magic(magic_function, '', """
print(y)""")
    assert_not_detected()
    get_ipython().run_cell_magic(magic_function, '', """
a = 1""")
    get_ipython().run_cell_magic(magic_function, '', """
print(y)""")
    assert_not_detected("Changing a should not affect y")

def test_func_assign_herlper_func2():
    new_test()
    get_ipython().run_cell_magic(magic_function, '', """
x = 3
a = 4
def f():
    def g():
        print(a)
        return x
    return g
y = f()()
""")
    get_ipython().run_cell_magic(magic_function, '', """
x = 4""")
    get_ipython().run_cell_magic(magic_function, '', """
print(y)""")
    assert_detected("Should have detected stale dependency of y on x")



#Run all above tests using ipytest
ipytest.run_tests()
