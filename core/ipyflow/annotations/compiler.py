# -*- coding: utf-8 -*-
"""
Compiles the annotations in .pyi files into handlers for library code.
"""
import ast
import logging
import os
import sys
from types import ModuleType
from typing import Dict, List, Type

from ipyflow.tracing.external_call_handler import (
    REGISTERED_HANDLER_BY_FUNCTION,
    CallerMutation,
    ExternalCallHandler,
    ModuleMutation,
    external_call_handler_by_name,
)
from ipyflow.utils.ast_utils import subscript_to_slice

logger = logging.getLogger(__name__)


REGISTERED_CLASS_SPECS: Dict[str, List[ast.ClassDef]] = {}
REGISTERED_FUNCTION_SPECS: Dict[str, List[ast.FunctionDef]] = {}


def compile_function_handler(
    func: ast.FunctionDef, is_method: bool
) -> Type[ExternalCallHandler]:
    # step 1: union arguments into groups such that any 2 args reference the same symbol somehow
    # step 2: for each group, create a handler that searches for a solution to the group constraint
    # step 3: combine the handlers into a single handler
    ret = func.returns
    if ret is None:
        raise TypeError(
            f"unable to handle null return type when trying to compile {func.name}"
        )
    if isinstance(ret, ast.Name):
        handler_type = external_call_handler_by_name.get(ret.id, None)
        if handler_type is None:
            raise ValueError(f"No known handler for return type {ret}")
        return handler_type
    elif isinstance(ret, ast.Subscript):
        sub_value = ret.value
        slice_value = subscript_to_slice(ret)
        if isinstance(sub_value, ast.Name):
            if sub_value.id == "Mutated":
                if isinstance(slice_value, ast.Name):
                    if slice_value.id == "__module__":
                        return ModuleMutation
                    elif slice_value.id == "self":
                        return CallerMutation
                    else:
                        raise ValueError(f"No known handler for return type {ret}")
            else:
                raise ValueError(f"No known handler for return type {ret}")
    else:
        raise TypeError(
            f"unable to handle return type {ret} when trying to compile {func.name}"
        )


def compile_class_handler(cls: ast.ClassDef) -> Dict[str, Type[ExternalCallHandler]]:
    handlers = {}
    for func in cls.body:
        if not isinstance(func, ast.FunctionDef):
            continue
        try:
            handlers[func.name] = compile_function_handler(func, is_method=True)
        except (ValueError, TypeError):
            # logger.exception(
            #     "exception while trying to compile handler for %s in class %s"
            #     % (func.name, cls.name)
            # )
            continue
    return handlers


def get_modules_from_decorators(decorators: List[ast.expr]) -> List[str]:
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


def compile_classes(
    classes: List[ast.ClassDef],
) -> Dict[str, Dict[str, Type[ExternalCallHandler]]]:
    handlers_by_class = {}
    for clazz in classes:
        handlers_by_class[clazz.name] = compile_class_handler(clazz)
    return handlers_by_class


def compile_functions(
    functions: List[ast.FunctionDef],
) -> Dict[str, Type[ExternalCallHandler]]:
    function_handlers = {}
    for func in functions:
        try:
            function_handlers[func.name] = compile_function_handler(
                func, is_method=False
            )
        except (ValueError, TypeError):
            # logger.exception(
            #     "exception while trying to compile handler for %s" % func.name
            # )
            continue
    return function_handlers


def register_annotations_file(filename: str) -> None:
    """
    Transforms the annotations in .pyi files into specs for later compilation into handlers for library code.
    """
    with open(filename, "r") as f:
        source = f.read()
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            continue
        for module in get_modules_from_decorators(node.decorator_list) or [
            os.path.splitext(os.path.basename(filename))[0]
        ]:
            if isinstance(node, ast.ClassDef):
                REGISTERED_CLASS_SPECS.setdefault(module, []).append(node)
            elif isinstance(node, ast.FunctionDef):
                REGISTERED_FUNCTION_SPECS.setdefault(module, []).append(node)


def compile_and_register_handlers_for_module(module: ModuleType) -> None:
    compiled_class_handlers = compile_classes(
        REGISTERED_CLASS_SPECS.get(module.__name__, [])
    )
    compiled_function_handlers = compile_functions(
        REGISTERED_FUNCTION_SPECS.get(module.__name__, [])
    )
    for classname, compiled_class_method_handlers in compiled_class_handlers.items():
        clazz = getattr(module, classname, None)
        if clazz is None:
            continue
        for method_name, handler in compiled_class_method_handlers.items():
            method_function = getattr(clazz, method_name, None)
            if method_function is not None:
                REGISTERED_HANDLER_BY_FUNCTION[method_function] = handler
    for function_name, handler in compiled_function_handlers.items():
        function = getattr(module, function_name, None)
        if function is not None:
            REGISTERED_HANDLER_BY_FUNCTION[function] = handler


def register_annotations_directory(dirname: str) -> None:
    annotation_files = []
    for filename in os.listdir(dirname):
        if os.path.splitext(filename)[1] == ".pyi":
            annotation_files.append(filename)
            register_annotations_file(os.path.join(dirname, filename))
    for filename in annotation_files:
        module = sys.modules.get(os.path.splitext(filename)[0])
        if module is not None:
            compile_and_register_handlers_for_module(module)


register_annotations_directory(os.path.dirname(__file__))
