from types import FunctionType, LambdaType
from typing import TYPE_CHECKING, Type, Union

from ipyflow.tracing.uninstrument import uninstrument

if TYPE_CHECKING:
    from cloudpickle.cloudpickle_fast import CloudPickler


def _function_reduce(self_, obj) -> None:
    pass


def _patched_function_reduce(
    self_: "CloudPickler", obj: Union[FunctionType, LambdaType]
) -> None:
    uninstrumented = uninstrument(obj)
    return _function_reduce(self_, obj if uninstrumented is None else uninstrumented)


def patch_cloudpickle_function_reduce(pickler_cls: Type["CloudPickler"]) -> None:
    global _function_reduce
    _function_reduce = pickler_cls._function_reduce
    pickler_cls._function_reduce = _patched_function_reduce
