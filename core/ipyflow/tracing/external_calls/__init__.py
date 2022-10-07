# -*- coding: utf-8 -*-
import ast
import logging
from types import ModuleType
from typing import Any, Optional

# force handler registration by exec()ing the handler modules here
import ipyflow.tracing.external_calls.base_handlers
import ipyflow.tracing.external_calls.list_handlers
from ipyflow.singletons import flow
from ipyflow.tracing.external_calls.base_handlers import (
    REGISTERED_HANDLER_BY_FUNCTION,
    ExternalCallHandler,
    MutatingMethodEventNotYetImplemented,
    NoopCallHandler,
    StandardMutation,
)


def resolve_external_call(
    module: Optional[ModuleType],
    caller_self: Optional[Any],
    function_or_method: Optional[Any],
    method: Optional[str],
    call_node: Optional[ast.Call] = None,
    use_standard_default: bool = True,
) -> Optional[ExternalCallHandler]:
    if caller_self is not None and isinstance(caller_self, ModuleType):
        if module is None:
            module = caller_self
        caller_self = None
    if (
        module is logging
        or getattr(module, "__name__", None) == "__main__"
        or function_or_method == print
    ):
        return None
    if caller_self is logging or isinstance(caller_self, logging.Logger):
        return None
    elif caller_self is not None and id(type(caller_self)) in flow().aliases:
        return None
    # TODO: handle case where it's a function defined in-notebook
    elif caller_self is None:
        pass
    elif method is None:
        return None
    else:
        function_or_method = getattr(type(caller_self), method, function_or_method)
    if isinstance(caller_self, ModuleType):
        caller_self = None

    external_call_type = REGISTERED_HANDLER_BY_FUNCTION.get(function_or_method, None)
    if external_call_type is None:
        if use_standard_default:
            external_call_type = StandardMutation
        else:
            return None
    elif external_call_type is NoopCallHandler:
        return None
    elif external_call_type is MutatingMethodEventNotYetImplemented:
        external_call_type = StandardMutation

    return external_call_type.create(
        module=module,
        caller_self=caller_self,
        function_or_method=function_or_method,
        call_node=call_node,
    )
