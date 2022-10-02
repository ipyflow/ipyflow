# -*- coding: utf-8 -*-
"""
Tools describing how non-notebook code affects dataflow
"""
import os
from typing import List, Set

from ipyflow.annotations.compiler import register_annotations_directory
from ipyflow.data_model.data_symbol import DataSymbol
from ipyflow.singletons import flow
from ipyflow.tracing.external_calls.base_handlers import (
    ExternalCallHandler,
    HasGetitem,
    MutatingMethodEventNotYetImplemented,
    NamespaceClear,
    NoopCallHandler,
)

register_annotations_directory(os.path.dirname(__file__))


# fake symbols just to prevent linter errors
self = __module__ = None


def handler_for(*_methods):
    """
    Just a marker decorator to indicate that the handler is used for functions / methods
    named differently from the decorated function / method
    """
    pass


def module(*_modules):
    """
    Just a marker decorator indicating that the class / function belongs to the
    module indicated in the parameter, as opposed to the one indicated by the file name
    of the containing .pyi file.
    """
    pass


class SymbolUpserted(ExternalCallHandler):
    """
    Stub for indicating that a value is upserted as a side effect.
    """


class Mutated(ExternalCallHandler):
    """
    Stub for indicating that a value is mutated as a side effect.
    """

    pass


class SymbolMatcher(metaclass=HasGetitem):
    """
    Indicates that the annotation matches a symbol
    """

    def matches(self, sym: DataSymbol) -> bool:
        return False


class AllOf(SymbolMatcher):
    """
    Indicates all of the subscripted matchers should match.
    """

    def __init__(self, matchers: List[SymbolMatcher]):
        self.matchers = matchers

    def matches(self, sym: DataSymbol) -> bool:
        return all(matcher.matches(sym) for matcher in self.matchers)


class AnyOf(SymbolMatcher):
    """
    Indicates that at least one of the subscripted matchers should match.
    If multiple match, the first wins.
    """

    def __init__(self, matchers: List[SymbolMatcher]):
        self.matchers = matchers

    def matches(self, sym: DataSymbol) -> bool:
        for matcher in self.matchers:
            if matcher.matches(sym):
                return True
        return False


class Display(SymbolMatcher):
    """
    Stub for referencing that a value represents stdout / stderr / display contents.
    """

    def matches(self, sym: DataSymbol) -> bool:
        return flow().display_sym is sym


class FileSystem(SymbolMatcher):
    """
    Stub for referencing that a value represents file system contents.
    """

    def __init__(self, fname: str) -> None:
        self.fname = fname

    def matches(self, sym: DataSymbol) -> bool:
        return sym.name == self.fname and sym.containing_namespace is flow().fs


class Children(SymbolMatcher):
    """
    Stub for referencing that an argument's subscripted values depend on it.
    """

    def __init__(self, children: Set[DataSymbol], exact: bool) -> None:
        self.children = children
        self.exact = exact

    def matches(self, sym: DataSymbol) -> bool:
        if self.exact:
            return sym.children.keys() == self.children
        else:
            return sym.children.keys() <= self.children


class Parents(SymbolMatcher):
    """
    Stub for referencing that an argument depends on the subscripted values.
    """

    def __init__(self, parents: Set[DataSymbol], exact: bool) -> None:
        self.parents = parents
        self.exact = exact

    def matches(self, sym: DataSymbol) -> bool:
        if self.exact:
            return sym.parents.keys() == self.parents
        else:
            return sym.parents.keys() <= self.parents


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
    Mutated,
    MutatingMethodEventNotYetImplemented,
    NamespaceClear,
    NoopCallHandler,
    Parents,
    SymbolUpserted,
    SymbolMatcher,
]
