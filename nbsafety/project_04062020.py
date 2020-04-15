from IPython.core.magic import register_cell_magic
import ast
import sys



#Program will raise this error instead of breaking
class UNEXPECTED_STATES(Exception):
    """There are three stages: Precheck, Run, Update.
        Visit_node represents this error happened in which method(e.g. visit_Assign) of the ast.NodeVisitor
        error_node is the ast node argument that caused this error
        msg is the extra msg to explain the error"""
    def __init__(self, state, visit_node, error_node, msg = ""):
        self.stage = state
        self.visit_node = visit_node
        self.error_node = error_node
        self.msg = msg


#################################################################################
############################  PreCheck Stage ####################################
#################################################################################
class PreCheck(ast.NodeVisitor):
    """This function should be called when we want to precheck an ast.Module. For each lane/blcok of the cell
        We first run the check of new assignments, then we obtain all the names. In these names, we put the ones
        that are user defined and not in the safe_set into the return check_set for further checks."""
    def precheck(self, module_node, scope):
        check_set = set()
        self.safe_set = set()
        self.current_scope = scope
        for node in module_node.body:
            self.visit(node)
            for name in GetAllNames().get_name_set(node):
                if name in self.current_scope.variable_dict and name not in self.safe_set:
                    check_set.add(name)
        return check_set


    """Helper function to remove the subscript and return the name node in front of the subscript
    For example: pass in ast.Subscript node "a[3][b][5]" will return ast.Name node "a"."""
    def remove_subscript(self, node):
        while isinstance(node, ast.Subscript):
            node = node.value
        return node


    """In case of assignment, we put the new assigned variable into a safe_set to indicate that we know 
        for sure it won't have stale dependency. 
    Note that node.targets might contain multiple ast.Name node in the case of "a = b = 3", so we go through
        each node in the targets.
    Also that target would be an ast.Tuple node in the case of "a,b = 3,4". Thus we need to break the tuple
        in that case """
    def visit_Assign(self, node):
        for target_node in node.targets:
            if isinstance(target_node, ast.Tuple):
                for element_node in target_node.elts:
                    element_node = self.remove_subscript(element_node)
                    if isinstance(element_node, ast.Name):
                        self.safe_set.add(element_node.id)
            target_node = self.remove_subscript(target_node)
            if isinstance(target_node, ast.Name):
                self.safe_set.add(target_node.id)
            else:
                raise UNEXPECTED_STATES("Precheck", "visit_Assign", target_node, "Expect to be ast.Tuple or ast.Name")


    #Similar to assignment, but multiple augassignment is not allowed
    def visit_AugAssign(self, node):
        target_node = self.remove_subscript(node.target)
        if isinstance(target_node, ast.Name):
            self.safe_set.add(target_node.id)
        else:
            raise UNEXPECTED_STATES("Precheck", "visit_AugAssign", target_node, "Expect to be ast.Name")


    #We also put the name of new functions in the safe_set
    def visit_FunctionDef(self, node):
        self.safe_set.add(node.name)

    def visit_For(self, node):
        #Case "for a,b in something: "
        if isinstance(node.target, ast.Tuple):
            for name_node in node.target.elts:
                if isinstance(name_node, ast.Name):
                    self.safe_set.add(name_node.id)
                else:
                    raise UNEXPECTED_STATES("Precheck", "visit_For", name_node, "Expect to be ast.Name")
        #case "for a in something"
        elif isinstance(node.target, ast.Name):
            self.safe_set.add(node.target.id)
        else:
            raise UNEXPECTED_STATES("Update", "visit_For", node.target, "Expect to be ast.Tuple or ast.Name")

        #Then we keep doing the visit for the body of the loop.
        for line in node.body:
            self.visit(line)


    

#Call GetAllNames().get_name_set(ast_tree) to get a set of all names appeared in ast_tree.
#Hleper Class
class GetAllNames(ast.NodeVisitor):
    #This function should be called when getting the name set. 
    def get_name_set(self, node):
        self.name_set = set()
        self.visit(node)
        return self.name_set


    def visit_Name(self, node):
        self.name_set.add(node.id)


    #We overwrite FunctionDef because we don't need to check names in the body of the definition. 
    #Only need to check for default arguments
    def visit_FunctionDef(self, node):
        if isinstance(node.args, ast.arguments):
            for default_node in node.args.defaults:
                self.visit(default_node)
        else:
            raise UNEXPECTED_STATES("Precheck Helper", "visit_FunctionDef", node.args, "Expect to be ast.arguments")



#################################################################################
############################  Run Stage  ########################################
#################################################################################
#The trace function we use to capture the frame dict of each scope. 
def capture_frame_at_run_time(frame, event, arg):
    original_frame = frame
    if 'ipython-input' in frame.f_code.co_filename:
        if event == "call":
            path = ()
            while frame.f_code.co_name != '<module>':
                path = (frame.f_code.co_name,) + path 
                frame = frame.f_back
            if path not in capture_frame_at_run_time.dictionary:
                capture_frame_at_run_time.dictionary[path] = original_frame



#################################################################################
#############################   Update Stage   ##################################
#################################################################################
class UpdateDependency(ast.NodeVisitor):
    """This function should be called when we are in the Update stage. This function will init the global_scope's frame_dict
        if it is never binded. Then it will bind the instance attribute current_scope to the scope passed in (usually the 
        global_scope). Then it will call super's visit function to visit everything in the module_node. Create or update node
        dependencies happened in this cell"""
    def updateDependency(self, module_node, scope):
        if scope.frame_dict is None:
            scope.frame_dict = capture_frame_at_run_time.dictionary[()].f_locals
        self.current_scope = scope
        self.visit(module_node)



    """Helper function that takes in a statement, returns a set that contains the dependency node set. 
       Typically on the right side of assignments. 
       For example: in a line "a = b + 3", the part "b + 3" will be the value argument. The return value will be a set
       contains the variable node "b" that was looked up in all scopes. 
    """
    def get_statement_dependency(self, value):
        #The check queue starts with the value node passed in
        queue = [value]
        #The return dependency set starts with an empty set
        return_dependency = set()

        #while queue is not empty
        while queue:
            node = queue.pop()

            #case "a", add the name to the return set. The base case
            if isinstance(node, ast.Name):
                return_dependency.add(self.current_scope.get_node_by_name_all_scope(node.id))
            #case "a + b", append both sides to the queue
            elif isinstance(node, ast.BinOp):
                queue.append(node.left)
                queue.append(node.right)
            #case "[a,b,c]", extend all elements of it to the queue
            elif isinstance(node, ast.List):
                queue.extend(node.elts)
            #case Function Calls
            elif isinstance(node, ast.Call):
                #Should initialize call_dependency if it is called first time
                self.visit_Call(node)

                """Get the function id in different cases, if it is a built-in function, we pass in the None's id to look 
                up in dependency_safety.func_id_to_scope_object. Since it can never contain id(None), this will automatically
                evaluates to false. This behavior ensures that we don't do anything to built-in functions except put all its
                arguments to check queue. We then obtain everything from the call_dependency and just put them in."""
                if isinstance(node.func, ast.Name):
                    func_id = id(self.current_scope.get_object_by_name_all_scope(node.func.id))
                elif isinstance(node.func, ast.Subscript):
                    func_id = id(self.get_subscript_object(node.func))

                if func_id not in dependency_safety.func_id_to_scope_object:
                    queue.extend(args)
                    #Should extend keywords too. Implement later together with the missing part in visit_Call about keywords
                
                else:
                    func_scope = dependency_safety.func_id_to_scope_object[func_id]
                    return_dependency.add(func_scope.parent_scope.get_node_by_name_all_scope(func_scope.scope_name))
                    #In call_dependency, an item could be an integer indicate a position in argument or a node directly
                    for item in func_scope.call_dependency:
                        if isinstance(item, int):
                            if item < len(node.args):
                                queue.append(node.args[item])
                        elif isinstance(item, VariableNode):
                            return_dependency.add(item)
        return return_dependency

    """Helper function to get the object in a ast.Subscript node"""
    def get_subscript_object(self, node):
        slice_list = []
        while isinstance(node, ast.Subscript):
            if not isinstance(node.slice, ast.Index):
                raise UNEXPECTED_STATES("Update", "visit_Subscript", node.slice, "Expected to be ast.Index")
            if isinstance(node.slice.value, ast.Num):
                slice_list.append(node.slice.value.n)
            elif isinstance(node.slice.value, ast.Name):
                slice_list.append(self.current_scope.frame_dict[node.slice.value.id])
            else:
                raise UNEXPECTED_STATES("Update", "visit_Subscript", node.slice.value, "Only support ast.Num and ast.Name for now")
            node = node.value

        if isinstance(node, ast.Name):
            name = node.id
            if name not in self.current_scope.frame_dict:
                return None
            ret_object = self.current_scope.frame_dict[name]
            for i in slice_list:
                ret_object = ret_object[i]
            return ret_object
        raise UNEXPECTED_STATES("Update", "visit_Subscript", node, "Only support ast.Name for now")


    """Helper function to remove the subscript and return the name node in front of the subscript
    For example: pass in ast.Subscript node "a[3][b][5]" will return ast.Name node "a"."""
    def remove_subscript(self, node):
        while isinstance(node, ast.Subscript):
            node = node.value
        return node


    #Assignments. Similar to the one in precheck. We go through each target in targets. Then if it is a tuple, we assign each value accordingly. 
    def visit_Assign(self, node):
        """
        Actually we cannot do the below greyed out part because there are different situations:
        a = 1      ----- LHS is ast.Name, RHS is ast.Num(or ast.Name or anything not ast.Tuple)
        a, b = c, 3 ---- LHs is ast.Tuple, RHS is ast.Tuple
        a = (1, 2) ----- LHS is ast.Name, RHS is ast.Tuple
        b, c = a   ----- LHS is ast.Tuple, RHS is ast.Name(or anything not ast.Tuple)
        a = b, c = (e, f) --- a will be assigned to a tuple and b,c are assigned to integers
                            Thus we should not precompute what the RHS dependency is until we enter the loop

        #Since we only have one value node, we can obtain the dependency first for efficiency, so that we don't need to run it for each target 
        if self.target isinstance(node.value, ast.Tuple):
            right_side_dependency = [self.get_statement_dependency(elt) for elt in node.value.elts]
        else:
            right_side_dependency = self.get_statement_dependency(node.value)
        """
        for target_node in node.targets:
            if isinstance(target_node, ast.Tuple):
                for i in range(len(target_node.elts)):
                    self.current_scope.update_node(target_node.elts[i].id, self.get_statement_dependency(node.value.elts[i]))
            elif isinstance(target_node, ast.Name):
                self.current_scope.update_node(target_node.id, self.get_statement_dependency(node.value))
            elif isinstance(target_node, ast.Subscript):
                name_node = self.remove_subscript(target_node)
                if not isinstance(name_node, ast.Name):
                    raise UNEXPECTED_STATES("Update", "visit_Assign", name_node, "Expect to be ast.Name")
                self.current_scope.update_node(name_node.id, self.get_statement_dependency(node.value))
            else:
                raise UNEXPECTED_STATES("Update", "visit_Assign", target_node, "Expect to be ast.Tuple, ast.Name or ast.Subscript")


    """For AugAssignments, we first remove subscripts to get the list name. Else, it is just a ast.Name node. We first get its orginal
        parent_node_set because we want to keep that relation in a AugAssignment. Then we update the node with the new dependencies"""
    def visit_AugAssign(self, node):
        if isinstance(node.target, ast.Subscript):
            name_node = self.remove_subscript(node.target)
        else:
            name_node = node.target
        if isinstance(name_node, ast.Name):
            #If the name is not user-defined, we just ignore
            if not self.current_scope.contains_name_current_scope(name_node.id):
                return
            dependency_nodes = self.get_statement_dependency(node.value)
            dependency_nodes.update(self.current_scope.get_node_by_name_current_scope(name_node.id).parent_node_set)
            self.current_scope.update_node(name_node.id, dependency_nodes)
        else:
            raise UNEXPECTED_STATES("Update", "visit_Assign", name_node, "Expect to be ast.Name or ast.Subscript")


    #For loops
    def visit_For(self, node):
        #Obtain the dependency created by the iter (The "in" part of the for loop)
        iter_dependency = self.get_statement_dependency(node.iter)
        #If it is an unpack situation, we update each variable's dependency
        if isinstance(node.target, ast.Tuple):
            for name_node in node.target.elts:
                if isinstance(name_node, ast.Name):
                    self.current_scope.update_node(name_node.id, iter_dependency)
                else:
                    raise UNEXPECTED_STATES("Update", "visit_For", name_node, "Expect to be ast.Name")
        #else if its just one name, we just update that name
        elif isinstance(node.target, ast.Name):
            self.current_scope.update_node(node.target.id, iter_dependency)
        #Otherwise, we reached an unexpected node
        else:
            raise UNEXPECTED_STATES("Update", "visit_For", node.target, "Expect to be ast.Tuple or ast.Name")

        #Then we keep doing the visit for the body of the loop.
        for line in node.body:
            self.visit(line)


    """Function definitions
    An argument node contains: args, vararg, kwonlyargs, kw_defaults, kwarg, defaults
    def func(args1, args2, defaults = 3, *vararg, kwonlyargs1, kwonlyargs2, kw_defaults = 3, **kwarg):
    """
    def visit_FunctionDef(self, node):
        #Create a new function scope of its name. Pass in the current scope as its parent scope
        func_scope = Scope(node.name, self.current_scope)

        #Store the scope by its ID in dependency_safety's function attribute
        dependency_safety.func_id_to_scope_object[id(self.current_scope.frame_dict[node.name])] = func_scope

        #Initialize the to_update_dependency dictionary for default arguments
        func_scope.to_update_dependency = dict()


        #We first initialize that this function is dependent on nothing. We will then add in dependencies of default arguments
        dependency = set()

        #We expect that FunctionDef.args is an ast.arguments, otherwise we raise an unexpected error
        if not isinstance(node.args, ast.arguments):
            raise UNEXPECTED_STATES("Update", "visit_FunctionDef, node.args", "Expect to be ast.arguments")
        argument_node = node.args

        #For each arg of defaults, we get its dependency then update the function node dependency and the argument dependency within the scope
        for i in range(len(argument_node.defaults), 0, -1):
            if not isinstance(argument_node.args[-i], ast.arg):
                raise UNEXPECTED_STATES("Update", "visit_FunctionDef", argument_node.args[-i], "Expect to be ast.arg")

            default_arg_dependency = self.get_statement_dependency(argument_node.defaults[-i])
            dependency.update(default_arg_dependency)
            #argument_node.args[-i].arg is the string name of the argument
            func_scope.to_update_dependency[argument_node.args[-i].arg] = set(default_arg_dependency)
        
        #Similar to above, but this is for kw_defaults
        for i in range(len(argument_node.kw_defaults)):
            if argument_node.kw_defaults[i]:
                if not isinstance(argument_node.kwonlyargs[i], ast.arg):
                    raise UNEXPECTED_STATES("Update", "visit_FunctionDef", argument_node.kwonlyargs[i], "Expect to be ast.arg")

                default_arg_dependency = self.get_statement_dependency(argument_node.kw_defaults[i])
                dependency.update(default_arg_dependency)
                #argument_node.kwonlyargs[i].arg is the string name of the argument
                func_scope.to_update_dependency[argument_node.kwonlyargs[i].arg] = set(default_arg_dependency)

        #Record the body for function calls. 
        func_scope.func_body = node.body
        func_scope.func_args = node.args

        #Update Node
        self.current_scope.update_node(node.name, dependency)


    """This method is supposed to initialize the call_dependency of a function call. Note that we treat a function as
        two different nodes. A function node and a funciton_call node. Function node is the object of that function. 
        Function_call node is more like a description of what dependency you will obtain by calling this function. Thus,
        this function is to help to get the call_dependency first time it ever runs. It should detect and return if there
        is already a call_dependency set"""
    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            if node.func.id not in self.current_scope.frame_dict:
                func_id = id(None)
            else:
                func_id = id(self.current_scope.frame_dict[node.func.id])
        elif isinstance(node.func, ast.Subscript):
            func_id = id(self.get_subscript_object(node.func))
        else:
            raise UNEXPECTED_STATES("Update", "visit_Call", node.func, "Only support ast.Name and ast.Subscript for now")

        #If this is not a user-defined function, we don't need to update the dependency within it
        if func_id not in dependency_safety.func_id_to_scope_object:
            return

        func_scope = dependency_safety.func_id_to_scope_object[func_id]

        #If the call_dependency is already binded, no need to run it again
        if func_scope.call_dependency is not None:
            return
        #else we initialize the call dependency to an empty set
        else:
            func_scope.call_dependency = set()

        #Link the frame_dict because this function has already ran now
        path, s = (), func_scope
        while s is not dependency_safety.global_scope:
            path = (s.scope_name,) + path
            s = s.parent_scope
        func_scope.frame_dict = capture_frame_at_run_time.dictionary[path].f_locals

        #Get body part and argument part from the scope object
        func_body = func_scope.func_body
        func_args = func_scope.func_args

        """save the pointers to original scope and dependencies so that we could do a recursive call 
        to visit and update the relation within the new function scope"""
        original_scope = self.current_scope

        #Change instance attribute "new_dependencies" and "current_scope" to be the new ones
        self.current_scope = func_scope

        #The simple arguments list.
        arg_name_list = []
        #Record all simple arguments(including defaults), create nodes for them
        for arg_node in func_args.args:
            if not isinstance(arg_node, ast.arg):
                raise UNEXPECTED_STATES("Update", "visit_Call", arg_node, "Expect to be ast.arg")
            arg_name_list.append(arg_node.arg)
            self.current_scope.update_node(arg_node.arg, set())
        #Record Vararg
        if func_args.vararg is not None:
            arg_node = func_args.vararg
            if not isinstance(arg_node, ast.arg):
                raise UNEXPECTED_STATES("Update", "visit_Call", arg_node, "Expect to be ast.arg")
            self.current_scope.update_node(arg_node.arg, set())
        #Record all kwonly arguments(including kw_defaults)
        for arg_node in func_args.kwonlyargs:
            if not isinstance(arg_node, ast.arg):
                raise UNEXPECTED_STATES("Update", "visit_Call", arg_node, "Expect to be ast.arg")
            self.current_scope.update_node(arg_node.arg, set())
        #Record kwarg
        if func_args.kwarg is not None:
            arg_node = func_args.kwarg
            if not isinstance(arg_node, ast.arg):
                raise UNEXPECTED_STATES("Update", "visit_Call", arg_node, "Expect to be ast.arg")
            self.current_scope.update_node(arg_node.arg, set())

        #Run each line in the function body
        for line in func_body:
            if isinstance(line, ast.Return):
                check_set = self.get_statement_dependency(line.value)
                closed_set = set()
                while check_set:
                    node = check_set.pop()
                    name, scope = node.name, node.scope
                    closed_set.add(name)
                    if scope is func_scope:
                        #If it is one of the arguments, put the index in, so that we know which one
                        #of the arguments we need to record. Reduce false positive for those non-used args
                        if name in arg_name_list:
                            func_scope.call_dependency.add(arg_name_list.index(name))
                        elif name == func_args.vararg:
                            func_scope.call_dependency.add(len(arg_name_list))
                        ######There should be a way to get keyword arguments here########
                        #for those still in the scope variables, we keep checking its parents
                        else:
                            check_set.update([x for x in node.parent_node_set if x not in closed_set])
                    #If it is dependent on something outside of the current scope, then put it in
                    elif func_scope.is_my_ancestor_scope(scope):
                        func_scope.call_dependency.add(node)
            else:
                self.visit(line)
        #Restore the scope
        self.current_scope = original_scope






class VariableNode:
    def __init__(self, name, defined_CN, scope, uid, aliasable):
        #The actual string name of the Node
        #Note that the VariableNode should be identified by its name, thus the name should never change
        self.name = str(name)

        #The Scope class it belongs to
        self.scope = scope

        #Set of parent node that this node is depended on
        self.parent_node_set = set()

        #Set of children node that are depended on this node
        self.children_node_set = set()

        #The cell number when this node is defined
        self.defined_CN = defined_CN

        #The cell number this node is required to have to not be considered as stale dependency
        #The Pair should contain (The required cell number, The ancestor node that was updated)
        self.required_CN_node_pair = (defined_CN, None)

        #The actual id of the object that this node represents. 
        self.uid = uid

        #If the node belongs to a set of alias nodes
        self.aliasable = aliasable

        """For example: list is aliasable. Two name can point to the same list.
        Integer is not aliasable because modifying one integer object cannot 
        affect any other integer object that has the same ID"""
        if aliasable:
            #The set of nodes that have the same ID. 
            #This should be retrieved from some global dictionary that contains this relation
            
            #####################INCOMPLETE###########################
            self.alias_set = None
            #####################INCOMPLETE###########################


    def update_CN_node_pair(self, pair):
        self.required_CN_node_pair = pair
        for n in self.children_node_set:
            n.update_CN_node_pair(pair)


class Scope:
    def __init__(self, scope_name, parent_scope = None):
        #The actual string name of the Scope 
        self.scope_name = scope_name

        #The parent scope of this scope. Set to None if this is the global scope.
        self.parent_scope = parent_scope

        #A "name->scope" dictionary that contains all its children scopes.
        self.children_scope_dict = {}

        #If there is a parent scope, then updates its children scope dictionary to add self in.
        if parent_scope:
            parent_scope.children_scope_dict[scope_name] = self

        #A "name->node" dictionary that contains all VariableNode in this scope
        self.variable_dict = {}

        #The actual f_locals dictionary in the frame that this represents.
        #This will not be initialized untill the actual frame runs. updateDependency.visit_Call will update this. 
        self.frame_dict = None

        """The dependency set that will be used when function scope is called.
        This will remain None until the scope is defined in UpdateDependency.visit_FunctionDef method.
        It contains either a string or a integer. String represents a outter scope variable name and integer 
        represents a position of the argument
        """
        self.call_dependency = None

        """This will remain None until the scope is defined in UpdateDependency.visit_FunctionDef method.
        This dictionary is to record dependency of default arguments at the time the function is defined. Wwe don't have the frame_dict 
        of this never ran function and we have to wait until a Call to this function to update the dependencies recorded in this 
        set. """
        self.to_update_dependency = None

        """This is the body of a function definition. It won't run until the function is called. Thus, we store it
        here and when the function is called, we can update the dependency within it.
        This will remain None until the scope is defined in the UpdateDependency.visit_FunctionDef."""
        self.func_body = None

        #This is the arguments of the funciton definition.
        self.func_args = None


    #Create a new VariableNode under the current scope and return the node
    def create_node(self, name):
        """The new created node takes the name passed as its name, the current cell number as its defined cell number,
        this current scope as its scope, the id of the object archieved from frame_dict as its id. Lastly check if it is 
        aliasable. 
        """
        node = VariableNode(name, dependency_safety.counter, self, id(self.frame_dict[name]), self.is_aliasable(name))

        #update the variable_dict
        self.variable_dict[name] = node
        return node

    #Give a set of parent nodes, update the current node accordingly. 
    def update_node(self, node_name, dependency_nodes):
        if self.contains_name_current_scope(node_name):
            node = self.get_node_by_name_current_scope(node_name)
        else:
            node = self.create_node(node_name)

        removed_parents = node.parent_node_set - dependency_nodes
        for n in removed_parents:
            n.children_node_set.remove(node)
            node.parent_node_set.remove(n)

        new_parents = dependency_nodes - node.parent_node_set
        for n in new_parents:
            n.children_node_set.add(node)
            node.parent_node_set.add(n)

        node.defined_CN = dependency_safety.counter
        node.update_CN_node_pair((dependency_safety.counter, node))


    #returns the VariableNode that is represented by the name passed in. 
    def get_node_by_name_current_scope(self, name):
        return self.variable_dict[name]

    #returns the VariableNode that is represented by the name passed in. Look up all ancestor scopes. 
    def get_node_by_name_all_scope(self, name):
        scope = self
        while scope:
            if name in scope.variable_dict:
                return scope.variable_dict[name]
            scope = scope.parent_scope


    #returns the object that is represented by the name passed in, return none if not existed. 
    def get_object_by_name_current_scope(self, name):
        if name in self.frame_dict:
            return self.frame_dict[name]
        return None

    #returns the object that is represented by the name passed in. Look up all ancestor scopes, return none if not existed. 
    def get_object_by_name_all_scope(self, name):
        scope = self
        while scope:
            if name in scope.frame_dict:
                return scope.frame_dict[name]
            scope = scope.parent_scope
        return None


    #returns a boolean value that indicates if the name represents a VariableNode in current scope
    def contains_name_current_scope(self, name):
        return name in self.variable_dict

    #returns a boolean value if name exists in the scope or all ancestor scopes.
    def contains_name_all_scope(self, name):
        scope = self
        while scope:
            if name in scope.variable_dict:
                return True
            scope = scope.parent_scope
        return False

    def is_my_ancestor_scope(self, ancestor):
        s = self.parent_scope
        while s:
            if s is ancestor:
                return True
            s = s.parent_scope
        return False

    #helper function to check if the object behind the name in the scope is aliasable. 
    def is_aliasable(self, name):
        ###### Currently Disabled ########
        return False
        ##################################
        obj = self.frame_dict[name]

        ##################### INCOMPLETE ###########################
        #There should be some check about the object to see that if it is "aliasable"
        if isinstance(obj, int) or isinstance(obj, str):
            aliasable = False
        elif isinstance(obj, list) or isinstance(obj, dict) or isinstance(obj, set):
            aliasable = True
        else:
            aliasable = False
        ##################### INCOMPLETE ###########################

        return aliasable


@register_cell_magic
def dependency_safety(line, cell):
    #We increase the counter by one each time this cell magic function is called
    dependency_safety.counter += 1

    #We get the ast.Module node by parsing the cell
    ast_tree = ast.parse(cell)

    ############## PreCheck ##############
    """Precheck process. First obtain the names that need to be checked. Then we check if their
        defined_CN is greater than or equal to required, if not we give a warning and return. """
    for name in PreCheck().precheck(ast_tree, dependency_safety.global_scope):
        node = dependency_safety.global_scope.get_node_by_name_current_scope(name)
        if node.defined_CN < node.required_CN_node_pair[0]:
            dependency_safety.warning(name, node.defined_CN, node.required_CN_node_pair)
            return

    ############## Run ##############
    sys.settrace(capture_frame_at_run_time)
    get_ipython().run_cell(cell)
    sys.settrace(None)

    ############## update ##############
    UpdateDependency().updateDependency(ast_tree, dependency_safety.global_scope)
    return





#Make sure to run this init function before using the magic cell
def dependency_safety_init():
    dependency_safety.counter = 1
    dependency_safety.global_scope = Scope("global")
    dependency_safety.warning = lambda name, defined_CN, pair: print(name, "was defined in cell", 
        defined_CN, "but its ancestor dependency node", pair[1].name, "was redefined in cell", pair[0], ".")
    
    dependency_safety.func_id_to_scope_object = {}

    capture_frame_at_run_time.dictionary = {}

dependency_safety_init()









