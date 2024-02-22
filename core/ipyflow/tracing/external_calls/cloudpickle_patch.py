import ast
from types import FunctionType, LambdaType
from typing import TYPE_CHECKING, Any, Dict, Type, Union

from ipyflow.singletons import flow

if TYPE_CHECKING:
    import astunparse
    from cloudpickle.cloudpickle_fast import CloudPickler
elif hasattr(ast, "unparse"):
    astunparse = ast
else:
    import astunparse


def _function_reduce(self_, obj) -> None:
    pass


def _patched_function_reduce(
    self_: "CloudPickler", obj: Union[FunctionType, LambdaType]
) -> None:
    for alias in flow().aliases.get(id(obj), []):
        if not alias.is_function and not alias.is_lambda:
            continue
        try:
            local_env: Dict[str, Any] = {}
            func_defn = astunparse.unparse(alias.func_def_stmt)
            if isinstance(alias.func_def_stmt, (ast.AsyncFunctionDef, ast.FunctionDef)):
                func_name = alias.func_def_stmt.name
            elif isinstance(alias.func_def_stmt, ast.Lambda):
                func_name = "lambda_sym"
                func_defn = f"{func_name} = {func_defn}"
            else:
                continue
            exec(func_defn, obj.__globals__, local_env)
            new_obj = local_env[func_name]
        except:  # noqa
            continue
        if isinstance(new_obj, (FunctionType, LambdaType)):
            obj = new_obj
            break
    return _function_reduce(self_, obj)


def patch_cloudpickle_function_getstate(pickler_cls: Type["CloudPickler"]) -> None:
    global _function_reduce
    _function_reduce = pickler_cls._function_reduce
    pickler_cls._function_reduce = _patched_function_reduce
