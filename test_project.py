import ipytest
from project_02172020 import *
ipytest.config(rewrite_asserts=True, magics=True)

"""
Use "ipython test_project.py" command to run this test.

Although py.test should also work fine, but the main project has to be ran in ipython 
enviroment, many functions will complain undefined otherwise. Importing things could 
solve this problem, but I decided to implement this ipytest since it is also something
from Ipython.
"""


#rewrite the warning from magic cell so that we know it prompts a warning. DETECTED should be set to false again after each time use
original_warning = test.warning
DETECTED = False
def better_warning(name,mucn,mark):
	global DETECTED
	DETECTED = True
	original_warning(name,mucn,mark)
test.warning = better_warning


def assert_detected(msg = ""):
	global DETECTED
	assert DETECTED, str(msg)
	DETECTED = False

def assert_not_detected(msg = ""):
	assert not DETECTED, str(msg)

def new_test():
	test.counter = 1
	test.global_scope = Scope("global")
	test.new_dependency = {}
	test.function_call_dependency = {}
	CheckDependency.current_scope = test.global_scope
	CheckName.current_scope = test.global_scope


#simple test about the basic assignment
def test_Basic_Assignment_Break():
	new_test()
	get_ipython().run_cell_magic('test', '', 'a = 1')
	get_ipython().run_cell_magic('test', '', 'b = 2')
	get_ipython().run_cell_magic('test', '', 'c = a+b')
	get_ipython().run_cell_magic('test', '', 'd = c+1')
	get_ipython().run_cell_magic('test', '', 'print(a,b,c,d)')
	#redefine a here but not c and d
	get_ipython().run_cell_magic('test', '', 'a = 7')
	get_ipython().run_cell_magic('test', '', 'print(a,b,c,d)')
	assert_detected("Did not detect that c's reference was changed")


	get_ipython().run_cell_magic('test', '', 'c = a+b')
	get_ipython().run_cell_magic('test', '', 'print(a,b,c,d)')
	assert_detected("Did not detect that d's reference was changed")


	get_ipython().run_cell_magic('test', '', 'd = c+1')
	get_ipython().run_cell_magic('test', '', 'print(a,b,c,d)')
	assert_not_detected("There should be no more dependency issue")

#Foo, bar example from the project prompt
def test_Foo_Bar_Example():
	new_test()
	get_ipython().run_cell_magic('test', '', 'def foo():\n    return 5\n\ndef bar():\n    return 7')
	get_ipython().run_cell_magic('test', '', 'funcs_to_run = [foo,bar]')
	get_ipython().run_cell_magic('test', '', 'accum = 0\nfor f in funcs_to_run:\n    accum += f()\nprint(accum)')
	
	#redefine foo here but not funcs_to_run
	get_ipython().run_cell_magic('test', '', 'def foo():\n    return 10\n\ndef bar():\n    return 7')
	get_ipython().run_cell_magic('test', '', 'accum = 0\nfor f in funcs_to_run:\n    accum += f()\nprint(accum)')
	assert_detected("Did not detect that funcs_to_run's reference was changed")


	get_ipython().run_cell_magic('test', '', 'funcs_to_run = [foo,bar]')
	get_ipython().run_cell_magic('test', '', 'accum = 0\nfor f in funcs_to_run:\n    accum += f()\nprint(accum)')
	assert_not_detected("There should be no more dependency issue")


#tests about variables that have same name but in different scope. There shouldn't be any extra dependency because of the name
def test_Variable_Scope():
	new_test()
	get_ipython().run_cell_magic('test', '', """
def func():
	x = 6
""")
	get_ipython().run_cell_magic('test', '', 'x = 7')
	get_ipython().run_cell_magic('test', '', 'y = x')
	get_ipython().run_cell_magic('test', '', 'z = func')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')

	#change x inside of the function, but not x outside of the function
	get_ipython().run_cell_magic('test', '', 'def func():\n    x = 10')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')
	assert_detected("Did not detect the dependency change in the function")

	get_ipython().run_cell_magic('test', '', 'y = x')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')
	assert_detected("Updating y should not solve the dependency change inside of function func")

	get_ipython().run_cell_magic('test', '', 'z = func')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')
	assert_not_detected("Updating z should solve the problem")

def test_Variable_Scope2():
	new_test()
	get_ipython().run_cell_magic('test', '', 'def func():\n    x = 6')
	get_ipython().run_cell_magic('test', '', 'x = 7')
	get_ipython().run_cell_magic('test', '', 'y = x')
	get_ipython().run_cell_magic('test', '', 'z = func')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')

	#change x outside of the function, but not inside of the function
	get_ipython().run_cell_magic('test', '', 'x = 10')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')
	assert_detected("Did not detect the dependency change outside of the function")

	get_ipython().run_cell_magic('test', '', 'z = func')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')
	assert_detected("Updating z should not solve the dependency change outside of function")

	get_ipython().run_cell_magic('test', '', 'y = x')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')
	assert_not_detected("Updating y should solve the problem")

def test_default_args():
	new_test()
	get_ipython().run_cell_magic('test', '', """
x = 7
def foo(y=x):
	return y + 5
""")
	get_ipython().run_cell_magic('test', '', 'a = foo()')
	assert_not_detected()
	get_ipython().run_cell_magic('test', '', 'x = 10')
	assert_not_detected()
	get_ipython().run_cell_magic('test', '', 'b = foo()')
	assert_detected("Should have detected stale dependency of fn foo() on x")
	
def test_Same_Pointer():
	new_test()
	#a and b are actually pointing to the same thing
	get_ipython().run_cell_magic('test', '', 'a = [7]')
	get_ipython().run_cell_magic('test', '', 'b = a')
	get_ipython().run_cell_magic('test', '', 'c = b + [5]')

	get_ipython().run_cell_magic('test', '', 'a[0] = 8')
	get_ipython().run_cell_magic('test', '', 'print(c)')
	assert_not_detected("The list b is pointing to is changed after a's list changed since they \
		are actually the same thing. So there should not be any warning towards to c")

def test_func_assign():
	new_test()
	get_ipython().run_cell_magic('test', '', """
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
	get_ipython().run_cell_magic('test', '', """
z = func(c)""")
	get_ipython().run_cell_magic('test', '', """
a = 4""")
	get_ipython().run_cell_magic('test', '', """
print(z)""")
	assert_detected("Should have detected stale dependency of fn func on a")
	get_ipython().run_cell_magic('test', '', """
def func(x, y = a):
    print(b)
    e = c+d
    f = x + y
    return f
z = func(c)
""")
	get_ipython().run_cell_magic('test', '', """
print(z)""")
	assert_not_detected()
	get_ipython().run_cell_magic('test', '', """
c = 3""")
	get_ipython().run_cell_magic('test', '', """
print(z)""")
	assert_detected("Should have detected stale dependency of z on c")
	get_ipython().run_cell_magic('test', '', """
z = func(c)""")
	get_ipython().run_cell_magic('test', '', """
print(z)""")
	assert_not_detected()
	get_ipython().run_cell_magic('test', '', """
b = 4""")
	get_ipython().run_cell_magic('test', '', """
d = 1""")
	assert_not_detected("Changing b and d should not affect z")





#Run all above tests using ipytest
ipytest.run_tests()
