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
            exec(astunparse.unparse(alias.stmt_node), obj.__globals__, local_env)
            if isinstance(alias.stmt_node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                new_obj = local_env[alias.stmt_node.name]
            elif isinstance(alias.stmt_node, ast.Assign) and isinstance(
                alias.stmt_node.targets[0], ast.Name
            ):
                new_obj = local_env[alias.stmt_node.targets[0].id]
            elif isinstance(alias.name, str):
                new_obj = local_env[alias.name]
            else:
                continue
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
