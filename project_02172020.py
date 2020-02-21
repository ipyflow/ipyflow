from IPython.core.magic import register_cell_magic
import ast

def debug_print(name, node):
	print("\n Visiting ", name)
	print(node.__dict__)


class CheckDependency(ast.NodeVisitor):
	def get_right_side_dependency(self, value, extension = []):
		checklist = [value]
		dependency = set()
		while checklist:
			node = checklist.pop()
			if isinstance(node, ast.BinOp):
				checklist.append(node.left)
				checklist.append(node.right)
			elif isinstance(node, ast.Name):
				dependency.add((node.id, self.current_scope))
			elif isinstance(node, ast.List):
				checklist.extend(node.elts)
			elif isinstance(node, ast.Call):
				dependency.add((node.func.id, self.current_scope))
				for item in test.function_call_dependency[node.func.id]:
					if isinstance(item, int):
						if item < len(node.args):
							checklist.append(node.args[item])
					else:
						dependency.add(item)

		dependency.update(extension)
		return dependency




	def visit_Assign(self, node):
		if isinstance(node.targets[0], ast.Tuple):
			for i in range(len(node.targets[0].elts)):
				 test.new_dependency[self.current_scope][node.targets[0].elts[i].id] = self.get_right_side_dependency(node.value.elts[i])
		else:
			 test.new_dependency[self.current_scope][node.targets[0].id] = self.get_right_side_dependency(node.value)

	def visit_AugAssign(self, node):
		test.new_dependency[self.current_scope][node.target.id] = self.get_right_side_dependency(node.value, self.current_scope.get_node_dependency_list(node.target.id))


	def visit_FunctionDef(self, node):
		func_scope = Scope(node.name, self.current_scope)

		test.new_dependency[func_scope] = {}
		test.new_dependency[self.current_scope][node.name] = set()
		arg_name_list = [arg.arg for arg in node.args.args]
		for name in arg_name_list:
			test.new_dependency[func_scope][name] = set()
		for i in range(len(node.args.defaults)):
			pair = (node.args.defaults[-1-i].id, self.current_scope)
			test.new_dependency[func_scope][node.args.args[-1-i].arg].add(pair)
			test.new_dependency[self.current_scope][node.name].add(pair)

		CheckDependency.current_scope = func_scope
		call_dependency = set()
		for line in node.body:
			if isinstance(line, ast.Return):
				check_set = self.get_right_side_dependency(line.value)
				while check_set:
					name, scope = check_set.pop()
					if name in arg_name_list:
						call_dependency.add(arg_name_list.index(name))
					if scope != self.current_scope:
						call_dependency.add((name,scope))
					elif not scope.contains_name_current_scope(name) and name not in test.new_dependency[scope]:
						call_dependency.add((name, scope))
					else:
						check_set.update(scope.get_node_dependency_list(name))
			else:
				CheckDependency().visit(line)

		CheckDependency.current_scope = func_scope.parent_scope
		test.function_call_dependency[node.name] = call_dependency


class CheckName(ast.NodeVisitor):
	def visit_Name(self,node):
		new_dep = test.new_dependency[self.current_scope]
		if node.id in self.current_scope.variable_dict and node.id not in new_dep and self.current_scope.contains_name_all_scope(node.id):
			test.checklist.append(self.current_scope.get_node_by_name_all_scope(node.id))



"""An example Variable Node foo contains:
	name    				foo
	scope 					global
	parent_node_set 		{bar, boo}
	children_node_set 		{a, b}
	defined_CN				3
	required_CN_node_pair 	(4, bar)
"""
class VariableNode:
	def __init__(self, name, defined_CN, scope):
		self.name = str(name)
		self.scope = scope
		self.parent_node_set = set()
		self.children_node_set = set()
		self.defined_CN = defined_CN
		self.required_CN_node_pair = (defined_CN, None)

	def add_new_parent(self, new_parent_node):
		self.parent_node_set.add(new_parent_node)
		new_parent_node.children_node_set.add(self)

	def remove_parent(self, parent):
		if parent not in self.parent_node_set:
			return
		self.parent_node_set.remove(parent)
		parent.children_node_set.remove(self)

	def add_new_parent_set(self, new_parent_set):
		to_be_added = new_parent_set - self.parent_node_set
		to_be_removed = self.parent_node_set - new_parent_set
		for node in to_be_removed:
			self.remove_parent(node)
		for node in to_be_added:
			self.add_new_parent(node)

	def update_required_cn_node_pair(self, pair):
		if self.required_CN_node_pair[0] >= pair[0]:
			return
		self.required_CN_node_pair = pair
		for child in self.children_node_set:
			child.update_required_cn_node_pair(pair)


class Scope:
	def __init__(self, scope_name, parent_scope = None):
		self.scope_name = scope_name
		self.parent_scope = parent_scope
		if parent_scope:
			parent_scope.children_scope_dict[scope_name] = self
		self.children_scope_dict = {}
		self.variable_dict = {}

	def contains_name_all_scope(self, name):
		current_scope = self
		while current_scope:
			if name in current_scope.variable_dict:
				return True
			current_scope = current_scope.parent_scope
		return False

	def get_node_by_name_all_scope(self, name):
		current_scope = self
		while current_scope:
			if name in current_scope.variable_dict:
				return current_scope.variable_dict[name]
			current_scope = current_scope.parent_scope
		raise KeyError("Cannot find " + name)

	def contains_name_current_scope(self, name):
		return name in self.variable_dict

	def get_node_by_name_current_scope(self, name):
		if self.contains_name_current_scope(name):
			return self.variable_dict[name]
		raise KeyError("Cannot find " + name)

	def create_node(self, name, current_CN):
		node = VariableNode(name, current_CN, self)
		self.variable_dict[name] = node
		return node

	def get_or_create_node(self, name, current_CN):
		if self.contains_name_current_scope(name):
			return self.variable_dict[name]
		else:
			return self.create_node(name, current_CN)

	def get_node_dependency_list(self, name):
		if name in test.new_dependency[self]:
			return list(test.new_dependency[self][name])
		else:
			ret = []
			for node in self.get_node_by_name_current_scope(name).parent_node_set:
				ret.append((node.name, node.scope))
			return ret

@register_cell_magic
def test(line, cell):
	test.counter += 1

	scope = test.global_scope
	test.new_dependency[scope] = {}
	test.checklist = []

	ast_tree = ast.parse(cell)
	CheckDependency().visit(ast_tree)
	CheckName().visit(ast_tree)
	test.checklist.sort(key = lambda node: node.defined_CN)
	for node in test.checklist:
		if node.defined_CN < node.required_CN_node_pair[0]:
			test.warning(node.name, node.defined_CN, node.required_CN_node_pair)
			return

	get_ipython().run_cell(cell)

	for name in test.new_dependency[scope]:
		node = scope.get_or_create_node(name, test.counter)
		node.defined_CN = test.counter
		node.update_required_cn_node_pair((test.counter, node))
		new_parent_set = set()
		for pair in test.new_dependency[scope][name]:
			parent_node = pair[1].get_node_by_name_all_scope(pair[0])
			new_parent_set.add(parent_node)
		node.add_new_parent_set(new_parent_set)


def warning(name, defined_CN, pair):
	print(name, "was defined in cell", defined_CN, "but its reference ", pair[1].name, "was redefined in cell", 
		pair[0], ".")


test.counter = 1
test.global_scope = Scope("global")
test.warning = warning
test.new_dependency = {}
test.function_call_dependency = {}

CheckDependency.current_scope = test.global_scope
CheckName.current_scope = test.global_scope









