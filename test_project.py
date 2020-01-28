import ipytest
import project
ipytest.config(rewrite_asserts=True, magics=True)

"""
Use "ipython test_project.py" command to run this test.

Although py.test should also work fine, but the main project has to be ran in ipython 
enviroment, many functions will complain undefined otherwise. Importing things could 
solve this problem, but I decided to implement this ipytest since it is also something
from Ipython.
"""


#rewrite the warning from magic cell so that we know it prompts a warning. DETECTED should be set to false again after each time use
original_warning = project.test.warning
DETECTED = False
def better_warning(name,mucn,mark):
	global DETECTED
	DETECTED = True
	original_warning(name,mucn,mark)
project.test.warning = better_warning


#simple test about the basic assignment
def test_Basic_Assignment_Break():
	global DETECTED
	DETECTED = False
	get_ipython().run_cell_magic('test', '', 'a = 1')
	get_ipython().run_cell_magic('test', '', 'b = 2')
	get_ipython().run_cell_magic('test', '', 'c = a+b')
	get_ipython().run_cell_magic('test', '', 'd = c+1')
	assert "a" in project.test.dag.dict.keys() and "b" in project.test.dag.dict.keys(), "a and b not recorded"
	
	get_ipython().run_cell_magic('test', '', 'print(a,b,c,d)')
	#redefine a here but not c and d
	get_ipython().run_cell_magic('test', '', 'a = 7')
	get_ipython().run_cell_magic('test', '', 'print(a,b,c,d)')
	assert DETECTED, "Did not detect that c's reference was changed"
	DETECTED = False

	get_ipython().run_cell_magic('test', '', 'c = a+b')
	get_ipython().run_cell_magic('test', '', 'print(a,b,c,d)')
	assert DETECTED, "Did not detect that d's reference was changed"
	DETECTED = False

	get_ipython().run_cell_magic('test', '', 'd = c+1')
	get_ipython().run_cell_magic('test', '', 'print(a,b,c,d)')
	assert not DETECTED, "There should be no more dependency issue"


#Foo, bar example from the project prompt
def test_Foo_Bar_Example():
	global DETECTED
	DETECTED = False
	get_ipython().run_cell_magic('test', '', 'def foo():\n    return 5\n\ndef bar():\n    return 7')
	#our current problem is that we did not analysis foo and bar as function variables into our dictionary
	assert "foo" in project.test.dag.dict.keys() and "bar" in project.test.dag.dict.keys(), "foo and bar not recorded"
	
	get_ipython().run_cell_magic('test', '', 'funcs_to_run = [foo,bar]')
	get_ipython().run_cell_magic('test', '', 'accum = 0\nfor f in funcs_to_run:\n    accum += f()\nprint(accum)')
	
	#redefine foo here but not funcs_to_run
	get_ipython().run_cell_magic('test', '', 'def foo():\n    return 10\n\ndef bar():\n    return 7')
	get_ipython().run_cell_magic('test', '', 'accum = 0\nfor f in funcs_to_run:\n    accum += f()\nprint(accum)')
	assert DETECTED, "Did not detect that funcs_to_run's reference was changed"
	DETECTED = False

	get_ipython().run_cell_magic('test', '', 'funcs_to_run = [foo,bar]')
	get_ipython().run_cell_magic('test', '', 'accum = 0\nfor f in funcs_to_run:\n    accum += f()\nprint(accum)')
	assert not DETECTED, "There should be no more dependency issue"


#tests about variables that have same name but in different scope. There shouldn't be any extra dependency because of the name
def test_Variable_Scope():
	global DETECTED
	DETECTED = False
	get_ipython().run_cell_magic('test', '', 'def func():\n    x = 6')
	get_ipython().run_cell_magic('test', '', 'x = 7')
	get_ipython().run_cell_magic('test', '', 'y = x')
	get_ipython().run_cell_magic('test', '', 'z = func')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')

	#change x inside of the function, but not x outside of the function
	get_ipython().run_cell_magic('test', '', 'def func():\n    x = 10')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')
	assert DETECTED, "Did not detect the dependency change in the function"
	DETECTED = False
	get_ipython().run_cell_magic('test', '', 'y = x')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')
	assert DETECTED, "Updating y should not solve the dependency change inside of function func"
	DETECTED = False
	get_ipython().run_cell_magic('test', '', 'z = func')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')
	assert not DETECTED, "Updating z should solve the problem"

def test_Variable_Scope2():
	global DETECTED
	DETECTED = False
	get_ipython().run_cell_magic('test', '', 'def func():\n    x = 6')
	get_ipython().run_cell_magic('test', '', 'x = 7')
	get_ipython().run_cell_magic('test', '', 'y = x')
	get_ipython().run_cell_magic('test', '', 'z = func')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')

	#change x outside of the function, but not inside of the function
	get_ipython().run_cell_magic('test', '', 'x = 10')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')
	assert DETECTED, "Did not detect the dependency change outside of the function"
	DETECTED = False
	get_ipython().run_cell_magic('test', '', 'z = func')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')
	assert DETECTED, "Updating z should not solve the dependency change outside of function"
	DETECTED = False
	get_ipython().run_cell_magic('test', '', 'y = x')
	get_ipython().run_cell_magic('test', '', 'print(y,z())')
	assert not DETECTED, "Updating y should solve the problem"



#Run all above tests using ipytest
ipytest.run_tests()
#After the tests, set the warning back to the original warning
project.test.warning = original_warning