import ast
import copy
import textwrap
from types import FunctionType, LambdaType
from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Union

from ipyflow.singletons import flow

if TYPE_CHECKING:
    import astunparse
elif hasattr(ast, "unparse"):
    astunparse = ast
else:
    import astunparse


def _make_uninstrumented_function(
    obj: Union[FunctionType, LambdaType],
    func_text: str,
    func_node: ast.AST,
    seen: Set[int],
):
    if isinstance(func_node, (ast.AsyncFunctionDef, ast.FunctionDef)):
        func_name = func_node.name
    elif isinstance(func_node, ast.Lambda):
        func_name = "lambda_sym"
        func_text = f"{func_name} = {func_text}"
    else:
        return None
    local_env: Dict[str, Any] = {}
    kwargs: Dict[str, Any] = {}
    func_text = textwrap.indent(func_text, "    ")
    if obj.__closure__ is not None:
        for cell_name, cell in zip(obj.__code__.co_freevars, obj.__closure__):
            kwargs[cell_name] = cell.cell_contents
    for name in obj.__code__.co_names:
        if name in obj.__globals__:
            referenced_global = obj.__globals__[name]
            uninstrumented = uninstrument(referenced_global, seen=seen)
            if uninstrumented is not None:
                kwargs[name] = uninstrumented
    func_text = f"""
def _Xix_make_closure({", ".join(kwargs.keys())}):
{func_text}
    return {func_name}
{func_name} = _Xix_make_closure(**kwargs)"""
    local_env["kwargs"] = kwargs
    try:
        exec(func_text, obj.__globals__, local_env)
        new_obj = local_env[func_name]
    except:  # noqa
        return None
    if hasattr(obj, "__name__") and hasattr(new_obj, "__name__"):
        new_obj.__name__ = obj.__name__
    if isinstance(new_obj, (FunctionType, LambdaType)):
        return new_obj
    else:
        return None


def _get_uninstrumented_decorator(obj: Union[FunctionType, LambdaType], seen: Set[int]):
    func_node, decorator_idx = flow().deco_metadata_by_obj_id.get(id(obj), (None, None))
    if func_node is None:
        return None
    func_node = copy.deepcopy(func_node)
    func_node.decorator_list = func_node.decorator_list[:decorator_idx]
    func_text = astunparse.unparse(func_node)
    return _make_uninstrumented_function(obj, func_text, func_node, seen)


def uninstrument(
    obj: Union[FunctionType, LambdaType], seen: Optional[Set[int]] = None
) -> Optional[Union[FunctionType, LambdaType]]:
    if seen is None:
        seen = set()
    if id(obj) in seen:
        return None
    seen.add(id(obj))
    try:
        new_obj = _get_uninstrumented_decorator(obj, seen)
    except:  # noqa
        new_obj = None
    if new_obj is not None:
        return new_obj
    for alias in flow().aliases.get(id(obj), []):
        if not alias.is_function and not alias.is_lambda:
            continue
        func_text = astunparse.unparse(alias.func_def_stmt)
        new_obj = _make_uninstrumented_function(
            obj, func_text, alias.func_def_stmt, seen
        )
        if new_obj is not None:
            return new_obj
    return None
