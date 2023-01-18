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
from typing import Dict, List, Optional, Set, Tuple, Type, Union

from ipyflow.annotations.annotations import Mutate, UpsertSymbol
from ipyflow.tracing.external_calls.base_handlers import (
    REGISTERED_HANDLER_BY_FUNCTION,
    REGISTERED_HANDLER_BY_METHOD,
    CallerMutation,
    CallerUpsert,
    ExternalCallHandler,
    ModuleMutation,
    ModuleUpsert,
    external_call_handler_by_name,
)
from ipyflow.utils.ast_utils import subscript_to_slice

logger = logging.getLogger(__name__)


REGISTERED_CLASS_SPECS: Dict[str, List[ast.ClassDef]] = {}
REGISTERED_FUNCTION_SPECS: Dict[str, List[ast.FunctionDef]] = {}


@functools.lru_cache(maxsize=None)
def _mutate_argument(
    pos: Optional[int], name: Optional[str], overwrite: bool = False
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
            dsym.update_deps(set(), overwrite=overwrite, mutated=not overwrite)

    return MutateArgument


def _make_multi_handler(
    handlers: List[Type[ExternalCallHandler]],
) -> Type[ExternalCallHandler]:
    class MultiHandler(ExternalCallHandler):
        def handle(self) -> None:
            for handler in handlers:
                handler.handle(self)

    return MultiHandler


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


def _make_mutate_name_handler(
    func: ast.FunctionDef, is_method: bool, name: str, overwrite: bool = False
) -> Type[ExternalCallHandler]:
    if name == "__module__":
        if overwrite:
            return ModuleUpsert
        else:
            return ModuleMutation
    elif name == "self":
        if is_method:
            if overwrite:
                return CallerUpsert
            else:
                return CallerMutation
    else:
        pos, is_posonly = _arg_position_in_signature(func, name, is_method=is_method)
        return _mutate_argument(
            pos=pos,
            name=None if is_posonly else name,
            overwrite=overwrite,
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
            if sub_value.id == Mutate.__name__ or sub_value.id == UpsertSymbol.__name__:
                overwrite = sub_value.id == UpsertSymbol.__name__
                if isinstance(slice_value, ast.Name):
                    return _make_mutate_name_handler(
                        func,
                        is_method=is_method,
                        name=slice_value.id,
                        overwrite=overwrite,
                    )
                elif isinstance(slice_value, ast.Tuple):
                    handlers = []
                    for elt in slice_value.elts:
                        if not isinstance(elt, ast.Name):
                            break
                        handlers.append(
                            _make_mutate_name_handler(
                                func,
                                is_method=is_method,
                                name=elt.id,
                                overwrite=overwrite,
                            )
                        )
                    else:
                        return _make_multi_handler(handlers)
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
        deco_func = decorator.func
        if not isinstance(deco_func, ast.Name) or deco_func.id != "handler_for":
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


def get_modules_from_decorators(decorators: List[ast.expr]) -> Set[str]:
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
    return set(modules)


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


def handle_string_annotation(
    node: Union[ast.Expr, ast.Str], filename: str
) -> Optional[Set[str]]:
    if isinstance(node, ast.Expr):
        if isinstance(node.value, ast.Str):
            node = node.value
        else:
            return None
    # validate that it's not too dangerous to call "eval" in the header
    header, contents = node.s.split("\n", 1)
    header = header[1:]
    parsed_header = ast.parse(header, mode="eval").body
    if not isinstance(parsed_header, ast.Compare):
        return None
    for comparator in [parsed_header.left] + parsed_header.comparators:
        if not isinstance(comparator, (ast.Attribute, ast.Tuple, ast.Num)):
            return None
        if isinstance(comparator, ast.Attribute):
            if not isinstance(comparator.value, ast.Name):
                return None
            if comparator.value.id != "sys":
                return None
            if comparator.attr != "version_info":
                return None
        elif isinstance(comparator, ast.Tuple):
            for elt in comparator.elts:
                if not isinstance(elt, ast.Num):
                    return None
    if eval(header):
        return register_annotations_from_source(contents, filename)
    else:
        return None


def register_annotations_from_source(source: str, filename: str) -> Set[str]:
    regisered_modules = set()
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.Expr, ast.Str)):
            continue
        for module in get_modules_from_decorators(
            getattr(node, "decorator_list", [])
        ) or [os.path.splitext(os.path.basename(filename))[0]]:
            regisered_modules.add(module)
            if isinstance(node, ast.ClassDef):
                REGISTERED_CLASS_SPECS.setdefault(module, []).append(node)
            elif isinstance(node, ast.FunctionDef):
                REGISTERED_FUNCTION_SPECS.setdefault(module, []).append(node)
            elif isinstance(node, (ast.Expr, ast.Str)):
                regisered_modules |= handle_string_annotation(node, filename) or set()
    return regisered_modules


def compile_handlers_for_already_imported_modules(modules: Set[str]) -> None:
    for module_name in modules:
        module = sys.modules.get(module_name)
        if module is not None:
            compile_and_register_handlers_for_module(module)


def register_annotations_file(
    filename: str, should_compile_handlers_for_already_imported_modules: bool = False
) -> Set[str]:
    """
    Transforms the annotations in .pyi files into specs for later compilation into handlers for library code.
    """
    with open(filename, "r") as f:
        source = f.read()
    modules = register_annotations_from_source(source, filename)
    if should_compile_handlers_for_already_imported_modules:
        compile_handlers_for_already_imported_modules(modules)
    return modules


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
            REGISTERED_HANDLER_BY_METHOD[clazz, method_name] = handler
            method_function = getattr(clazz, method_name, None)
            if method_function is not None:
                REGISTERED_HANDLER_BY_FUNCTION[method_function] = handler
    for function_name, handler in compiled_function_handlers.items():
        function = getattr(module, function_name, None)
        if function is not None:
            REGISTERED_HANDLER_BY_FUNCTION[function] = handler


def register_annotations_directory(dirname: str) -> Set[str]:
    registered_modules = set()
    annotation_files = []
    for filename in os.listdir(dirname):
        if os.path.splitext(filename)[1] == ".pyi":
            annotation_files.append(filename)
            registered_modules |= register_annotations_file(
                os.path.join(dirname, filename),
                should_compile_handlers_for_already_imported_modules=False,
            )
    for module_name in registered_modules:
        module = sys.modules.get(module_name)
        if module is not None:
            compile_and_register_handlers_for_module(module)
    return registered_modules
