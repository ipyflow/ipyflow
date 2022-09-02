# -*- coding: utf-8 -*-
"""
Tools describing how non-notebook code affects dataflow
"""
import abc
from typing import List, Set

from ipyflow.data_model.data_symbol import DataSymbol
from ipyflow.singletons import flow
from ipyflow.tracing.external_call_handler import ExternalCallHandler


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


class SymbolMatcher(abc.ABC):
    """
    Indicates that the annotation matches a symbol
    """

    @abc.abstractmethod
    def matches(self, sym: DataSymbol) -> bool:
        pass


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
