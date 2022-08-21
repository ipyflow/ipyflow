# -*- coding: utf-8 -*-
"""
Compiles the annotations in .pyi files into handlers for library code.
"""
import ast
from collections import defaultdict
from typing import Callable, Dict, List, Tuple


def compile_function_handler(func: ast.FunctionDef) -> Callable:
    # step 1: union arguments into groups such that any 2 args reference the same symbol somehow
    # step 2: for each group, create a handler that searches for a solution to the group constraint
    # step 3: combine the handlers into a single handler
    pass


def compile_class_handler(cls: ast.ClassDef) -> List[Callable]:
    pass


def get_modules_from_decorators(decorators: List[ast.Call]) -> List[str]:
    modules = []
    for decorator in decorators:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if (
            not isinstance(func, ast.Name)
            or func.id != "module"
            or len(decorator.args) != 1
        ):
            continue
        for arg in decorator.args:
            if isinstance(arg, ast.Str):
                modules.append(arg.s)
    return modules


def get_specs_by_module(
    filenames: List[str],
) -> Tuple[Dict[str, List[ast.ClassDef]], Dict[str, List[ast.FunctionDef]]]:
    """
    Compiles the annotations in .pyi files into handlers for library code.
    """
    if isinstance(filenames, str):
        filenames = [filenames]
    classes_by_module = defaultdict(list)
    functions_by_module = defaultdict(list)
    for fname in filenames:
        with open(fname, "r") as f:
            source = f.read()
        for node in ast.parse(source).body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                continue
            for module in get_modules_from_decorators(node.decorator_list) or [
                os.path.splitext(fname)[0]
            ]:
                if isinstance(node, ast.ClassDef):
                    classes_by_module[module].append(node)
                elif isinstance(node, ast.FunctionDef):
                    functions_by_module[module].append(node)
    return classes_by_module, functions_by_module


def compile_module_classes(
    classes_by_module: Dict[str, List[ast.ClassDef]],
) -> Dict[str, Dict[str, List[Callable]]]:
    class_handlers_by_module = defaultdict(dict)
    for module, classes in classes_by_module.items():
        for clazz in classes:
            class_handlers_by_module[module][clazz.name] = compile_class_handler(clazz)
    return class_handlers_by_module


def compile_module_functions(
    functions_by_module: Dict[str, List[ast.FunctionDef]],
) -> Dict[str, List[Callable]]:
    function_handlers_by_module = defaultdict(list)
    for module, functions in functions_by_module.items():
        for func in functions:
            function_handlers_by_module[module].append(compile_function_handler(func))
    return function_handlers_by_module
