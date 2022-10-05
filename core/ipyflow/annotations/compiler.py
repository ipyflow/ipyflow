# -*- coding: utf-8 -*-
"""
Compiles the annotations in .pyi files into handlers for library code.
"""
import ast
import functools
import logging
import os
import sys
from types import ModuleType
from typing import Dict, List, Optional, Tuple, Type

from ipyflow.tracing.external_calls.base_handlers import (
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


@functools.lru_cache(maxsize=None)
def _mutate_argument(
    pos: Optional[int], name: Optional[str]
) -> Type[ExternalCallHandler]:
    if pos is None and name is None:
        raise ValueError("pos and name cannot both be None")

    class MutateArgument(ExternalCallHandler):
        def handle(self) -> None:
            if name is not None and name in self.kwargs:
                dsyms = self.kwargs[name][1]
            elif pos is not None:
                dsyms = self.args[pos][1] if pos < len(self.args) else {None}
            else:
                return
            if len(dsyms) == 0:
                return
            dsym = next(iter(dsyms))
            if dsym is None:
                return
            dsym.update_deps(set(), overwrite=False, mutated=True)

    return MutateArgument


def _arg_position_in_signature(
    func: ast.FunctionDef, arg_name: str, is_method: bool
) -> Tuple[Optional[int], bool]:
    posonlyargs = getattr(func.args, "posonlyargs", [])
    for i, arg in enumerate(posonlyargs + func.args.args + func.args.kwonlyargs):
        if arg.arg == arg_name:
            return None if i >= len(posonlyargs) + len(
                func.args.args
            ) else i - is_method, i < len(posonlyargs)
    raise ValueError(
        "arg %s not found in function signature %s" % (arg_name, ast.dump(func))
    )


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
                        if is_method:
                            return CallerMutation
                    else:
                        pos, is_posonly = _arg_position_in_signature(
                            func, slice_value.id, is_method=is_method
                        )
                        return _mutate_argument(
                            pos=pos,
                            name=None if is_posonly else slice_value.id,
                        )
            raise ValueError(f"No known handler for return type {ret}")
    else:
        raise TypeError(
            f"unable to handle return type {ret} when trying to compile {func.name}"
        )


def get_names_for_function(func: ast.FunctionDef) -> List[str]:
    names = []
    for decorator in func.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if not isinstance(func, ast.Name) or func.id != "handler_for":
            continue
        for arg in decorator.args:
            if isinstance(arg, ast.Str):
                names.append(arg.s)
    if len(names) > 0:
        return names
    else:
        return [func.name]


def compile_class_handler(cls: ast.ClassDef) -> Dict[str, Type[ExternalCallHandler]]:
    handlers = {}
    for func in cls.body:
        if not isinstance(func, ast.FunctionDef):
            continue
        try:
            func_handler = compile_function_handler(func, is_method=True)
            for name in get_names_for_function(func):
                handlers[name] = func_handler
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
            func_handler = compile_function_handler(func, is_method=False)
            for name in get_names_for_function(func):
                function_handlers[name] = func_handler
        except (ValueError, TypeError):
            # logger.exception(
            #     "exception while trying to compile handler for %s" % func.name
            # )
            continue
    return function_handlers


def register_annotations_from_source(source: str, filename: str) -> None:
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.Expr, ast.Str)):
            continue
        for module in get_modules_from_decorators(
            getattr(node, "decorator_list", [])
        ) or [os.path.splitext(os.path.basename(filename))[0]]:
            if isinstance(node, ast.ClassDef):
                REGISTERED_CLASS_SPECS.setdefault(module, []).append(node)
            elif isinstance(node, ast.FunctionDef):
                REGISTERED_FUNCTION_SPECS.setdefault(module, []).append(node)
            elif isinstance(node, (ast.Expr, ast.Str)):
                if isinstance(node, ast.Expr):
                    if isinstance(node.value, ast.Str):
                        node = node.value
                    else:
                        continue
                # validate that it's not too dangerous to call "eval" in the header
                header, contents = node.s.split("\n", 1)
                header = header[1:]
                parsed_header = ast.parse(header, mode="eval").body
                if not isinstance(parsed_header, ast.Compare):
                    continue
                should_skip = False
                for comparator in [parsed_header.left] + parsed_header.comparators:
                    if not isinstance(comparator, (ast.Attribute, ast.Tuple, ast.Num)):
                        should_skip = True
                        break
                    if isinstance(comparator, ast.Attribute):
                        if not isinstance(comparator.value, ast.Name):
                            should_skip = True
                            break
                        if comparator.value.id != "sys":
                            should_skip = True
                            break
                        if comparator.attr != "version_info":
                            should_skip = True
                            break
                    elif isinstance(comparator, ast.Tuple):
                        for el in comparator.elts:
                            if not isinstance(el, ast.Num):
                                should_skip = True
                        if should_skip:
                            break
                if should_skip:
                    continue
                elif eval(header):
                    register_annotations_from_source(contents, filename)


def register_annotations_file(filename: str) -> None:
    """
    Transforms the annotations in .pyi files into specs for later compilation into handlers for library code.
    """
    with open(filename, "r") as f:
        source = f.read()
    register_annotations_from_source(source, filename)


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
