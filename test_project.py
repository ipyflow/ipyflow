import ipytest
import project
ipytest.config(rewrite_asserts=True, magics=True)

def test_Basic_Assignment_Break():
	original_warning = project.test.warning
	def better_warning(name,mucn,mark):
		test_Basic_Assignment_Break.detected = True
		original_warning(name,mucn,mark)
	test_Basic_Assignment_Break.detected = False
	project.test.warning = better_warning

	get_ipython().run_cell_magic('test', '', 'a = 1')
	get_ipython().run_cell_magic('test', '', 'b = 2')
	get_ipython().run_cell_magic('test', '', 'c = a+b')
	get_ipython().run_cell_magic('test', '', 'd = c+1')
	get_ipython().run_cell_magic('test', '', 'print(a,b,c,d)')
	get_ipython().run_cell_magic('test', '', 'a = 7')
	get_ipython().run_cell_magic('test', '', 'print(a,b,c,d)')
	assert test_Basic_Assignment_Break.detected, "Did not detect that c's reference was changed"
	
	test_Basic_Assignment_Break.detected = False
	get_ipython().run_cell_magic('test', '', 'c = a+b')
	get_ipython().run_cell_magic('test', '', 'print(a,b,c,d)')
	assert test_Basic_Assignment_Break.detected, "Did not detect that d's reference was changed"

	project.test.warning = original_warning









ipytest.run_tests()