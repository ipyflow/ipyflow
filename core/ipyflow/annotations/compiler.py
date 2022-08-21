# -*- coding: utf-8 -*-
"""
Compiles the annotations in .pyi files into handlers for library code.
"""
import ast
from collections import defaultdict
from typing import Dict, List, Tuple

from ipyflow.tracing.external_call_handler import ExternalCallHandler, NoopCallHandler


def compile_function_handler(func: ast.FunctionDef) -> ExternalCallHandler:
    # step 1: union arguments into groups such that any 2 args reference the same symbol somehow
    # step 2: for each group, create a handler that searches for a solution to the group constraint
    # step 3: combine the handlers into a single handler
    ret = func.returns
    if ret is None:
        raise TypeError(
            f"unable to handle null return type when trying to compile {func.name}"
        )
    if isinstance(ret, ast.Name):
        if ret.id == "NoopCallHandler":
            return NoopCallHandler()
        else:
            raise ValueError(f"No known handler for return type {ret}")
    elif isinstance(ret, ast.Subscript):
        pass
    else:
        raise TypeError(
            f"unable to handle return type {ret} when trying to compile {func.name}"
        )


def compile_class_handler(cls: ast.ClassDef) -> List[ExternalCallHandler]:
    handlers = []
    for func in cls.body:
        if not isinstance(func, ast.FunctionDef):
            continue
        handlers.append(compile_function_handler(func))
    return handlers


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
    Transforms the annotations in .pyi files into specs for later compilation into handlers for library code.
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


def compile_classes(
    classes: List[ast.ClassDef],
) -> Dict[str, List[ExternalCallHandler]]:
    handlers_by_class = {}
    for clazz in classes:
        handlers_by_class[clazz.name] = compile_class_handler(clazz)
    return handlers_by_class


def compile_functions(functions: List[ast.FunctionDef]) -> List[ExternalCallHandler]:
    function_handlers = []
    for func in functions:
        function_handlers[module].append(compile_function_handler(func))
    return function_handlers
