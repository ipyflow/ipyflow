# -*- coding: utf-8 -*-
"""
DSL describing how non-notebook code affects dataflow
"""
import os

from ipyflow.annotations.annotations import (
    AllOf,
    AnyOf,
    Children,
    Display,
    FileSystem,
    Mutate,
    Parents,
    SymbolMatcher,
    UpsertSymbol,
    __module__,
    handler_for,
    module,
    self,
)
from ipyflow.annotations.compiler import register_annotations_directory
from ipyflow.tracing.external_calls.base_handlers import (
    MutatingMethodEventNotYetImplemented,
    NamespaceClear,
    NoopCallHandler,
)

register_annotations_directory(os.path.dirname(__file__))


__all__ = [
    handler_for,
    module,
    self,
    __module__,
    AllOf,
    AnyOf,
    Display,
    FileSystem,
    Children,
    Mutate,
    MutatingMethodEventNotYetImplemented,
    NamespaceClear,
    NoopCallHandler,
    Parents,
    UpsertSymbol,
    SymbolMatcher,
]
