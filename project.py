#!/usr/bin/env python
# coding: utf-8

# In[1]:
from IPython.core.magic import register_cell_magic
import ast
class CheckDependency(ast.NodeTransformer):
    def helper(self, target, value):
        if isinstance(target, ast.Name):
            n = target.id
            #print(n, "is Recorded!")
            lst = [value]
            dependency = set()
            while lst:
                v = lst.pop()
                if isinstance(v, ast.BinOp):
                    lst.append(v.left)
                    lst.append(v.right)
                elif isinstance(v, ast.Name):
                    dependency.add(v.id)
                elif isinstance(v, ast.List):
                    lst.extend(v.elts)
            #print(n, "is dependent on", dependency)
            test.toUpdate[n] = set(dependency)
            
        
        
        
    def visit_Assign(self, node):
        #print("\nvisit_Assign")
        if isinstance(node.targets[0], ast.Tuple):
            for i in range(len(node.targets[0].elts)):
                self.helper(node.targets[0].elts[i], node.value.elts[i])
        else:
            self.helper(node.targets[0], node.value)
      
        #print(node.__dict__)
        #print(node.targets[0].__dict__)
        #print(node.value.__dict__)
        
        return node
    
    
    def visit_AugAssign(self, node):
        #print("\nvisit_AugAssign")
        
        self.helper(node.target, node.value)

        #print(node.__dict__)
        #print(node.target.__dict__)
        #print(node.value.__dict__)
        return node

    
    
class CheckName(ast.NodeTransformer):
    def visit_Name(self, node):
        if node.id not in test.toUpdate.keys() and node.id in test.dag.dict.keys():
            test.nameNodeList.append(test.dag.dict[node.id])
        return node

    
    
class VariableNode:
    def __init__(self, name, MUCN):
        self.name = str(name)
        self.MUCN = MUCN
        self.parentSet = set()
        self.referenceSet = set()
        self.mark = MUCN


class DAG:
    def __init__(self):
        self.dict = {}

    def updateNode(self, name, MUCN, newParentSet):
        if name not in self.dict.keys():
            node = VariableNode(name,MUCN)
            self.dict[name] = node
            node.parentSet = set(newParentSet)
            for parent in node.parentSet:
                self.dict[parent].referenceSet.add(name)
            return

        node = self.dict[name]
        node.MUCN = MUCN
        node.mark = MUCN

        setToMark = set(node.referenceSet)
        closedSet = set()
        while setToMark:
            nextName = setToMark.pop()
            nextNode = self.dict[nextName]
            closedSet.add(nextName)
            if MUCN > nextNode.mark:
                nextNode.mark = MUCN
                for n in nextNode.referenceSet:
                    if n not in closedSet:
                        setToMark.add(n)
        
        for parent in node.parentSet:
            self.dict[parent].referenceSet.remove(name)
        node.parentSet = set(newParentSet)
        for parent in node.parentSet:
            self.dict[parent].referenceSet.add(name)
            
    

# In[2]:

def warning(name, MUCN, mark):
    print(name, "is defined in cell", MUCN, "but its reference was redefined in cell",
                 mark, ". If you insist to run the cell, please rerun the cell with %%testRUN")



@register_cell_magic
def test(line,cell):
    test.counter += 1
    print("cell number", test.counter)
    
    test.toUpdate = {}
    test.nameNodeList = []
    
    tree = ast.parse(cell)
    CheckDependency().visit(tree)
    CheckName().visit(tree)
    test.nameNodeList.sort(key = lambda node: node.MUCN)
    for node in test.nameNodeList:
        if node.MUCN < node.mark:
            test.warning(node.name, node.MUCN, node.mark)
            return
        
    get_ipython().run_cell(cell)
    
    for name in test.toUpdate.keys():
        test.dag.updateNode(name, test.counter, test.toUpdate[name])
        
    
test.counter = 2
test.dag = DAG()
test.warning = warning

@register_cell_magic
def testRUN(line,cell):
    test.counter += 1
    tree = ast.parse(cell)
    test.toUpdate = {}
    test.nameSet = set()
    
    CheckDependency().visit(tree)
    get_ipython().run_cell(cell)
    for name in test.toUpdate.keys():
        test.dag.updateNode(name, test.counter, test.toUpdate[name])



