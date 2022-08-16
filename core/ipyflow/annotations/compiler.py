# -*- coding: utf-8 -*-
"""
Compiles the annotations in .pyi files into handlers for library code.
"""
import ast
from typing import Callable, Dict, List, Tuple


def compile_function_handler(func: ast.FunctionDef) -> Callable:
    # step 1: union arguments into groups such that any 2 args reference the same symbol somehow
    # step 2: for each group, create a handler that searches for a solution to the group constraint
    # step 3: combine the handlers into a single handler
    pass


def compile_class_handler(cls: ast.ClassDef):
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


def compile(
    fname: str,
) -> Dict[str, Tuple[Dict[str, Callable], Dict[str, Dict[str, Callable]]]]:
    """
    Compiles the annotations in .pyi files into handlers for library code.
    """
    with open(fname, "r") as f:
        source = f.read()
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            continue
        modules = get_modules_from_decorators(node.decorator_list) or [fname]
        if isinstance(node, ast.ClassDef):
            pass
        elif isinstance(node, ast.FunctionDef):
            pass
