# -*- coding: utf-8 -*-
import os
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from ipyflow.data_model.data_symbol import DataSymbol
from ipyflow.singletons import flow
from ipyflow.tracing.external_calls.base_handlers import ExternalCallHandler, HasGetitem

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


class UpsertSymbol(ExternalCallHandler):
    """
    Stub for indicating that a value is upserted as a side effect.
    """


class Mutate(ExternalCallHandler):
    """
    Stub for indicating that a value is mutated as a side effect.
    """

    pass


class SymbolMatcher(metaclass=HasGetitem):
    """
    Indicates that the annotation matches a symbol.
    """

    def __init__(self, bind_name: str) -> None:
        self.bind_name = bind_name

    def matches(self, sym: DataSymbol) -> Optional[Dict[str, Any]]:
        """
        :param sym: the symbol against which the matcher is being tested
        :return: if `sym` matches, a dictionary (possibly empty) of any additional bindings; otherwise, None.
        """
        return {self.bind_name: sym}


class Ref(SymbolMatcher):
    def __init__(self, bind_name: str) -> None:
        super().__init__(bind_name)
        self.existing_bindings = {}

    def matches(self, sym: DataSymbol) -> Optional[Dict[str, Any]]:
        if self.existing_bindings.get(self.bind_name) is sym:
            return {}
        else:
            return None


class AllOf(SymbolMatcher):
    """
    Indicates all of the subscripted matchers should match.
    """

    def __init__(self, bind_name: str, matchers: List[SymbolMatcher]) -> None:
        super().__init__(bind_name)
        self.matchers = matchers

    def matches(self, sym: DataSymbol) -> Optional[Dict[str, Any]]:
        bindings = {}
        for matcher in self.matchers:
            matcher_bindings = matcher.matches(sym)
            if matcher_bindings is None:
                return None
            else:
                bindings.update(matcher_bindings)
        bindings[self.bind_name] = sym
        return bindings


class AnyOf(SymbolMatcher):
    """
    Indicates that at least one of the subscripted matchers should match.
    If multiple match, the first wins.
    """

    def __init__(self, bind_name: str, matchers: List[SymbolMatcher]) -> None:
        super().__init__(bind_name)
        self.matchers = matchers

    def matches(self, sym: DataSymbol) -> Optional[Dict[str, Any]]:
        bindings = None
        for matcher in self.matchers:
            matcher_bindings = matcher.matches(sym)
            if matcher_bindings is not None:
                bindings = bindings or {}
                bindings.update(matcher_bindings)
        bindings[self.bind_name] = sym
        return bindings


class Display(SymbolMatcher):
    """
    Stub for referencing that a value represents stdout / stderr / display contents.
    """

    def matches(self, sym: DataSymbol) -> Optional[Dict[str, Any]]:
        if flow().display_sym is sym:
            return super().matches(sym)
        else:
            return None


class FileSystem(SymbolMatcher):
    """
    Stub for referencing that a value represents file system contents.
    """

    def __init__(self, bind_name: str, is_literal: bool = False) -> None:
        super().__init__(bind_name)
        self.is_literal = is_literal

    def matches(self, sym: DataSymbol) -> Optional[Dict[str, Any]]:
        if sym.containing_namespace is not flow().fs:
            return None
        if self.is_literal:
            return (
                {}
                if os.path.abspath(sym.name) == os.path.abspath(self.bind_name)
                else None
            )
        else:
            return {self.bind_name: sym.obj}


class MatcherRelation(Enum):
    CHILDREN = "children"
    PARENTS = "parents"


class _Relation(SymbolMatcher):
    """
    Matcher for indicating a dependency relationship between the symbol and the subscripted values
    """

    def __init__(
        self,
        bind_name: str,
        relation: MatcherRelation,
        matchers: List[SymbolMatcher],
        exact: bool,
    ) -> None:
        super().__init__(bind_name)
        self.relation = relation
        self.matchers = matchers
        self.exact = exact
        self._remaining_symbols: Optional[Set[DataSymbol]] = None

    def _matches_helper(self, idx: int) -> Optional[Dict[str, Any]]:
        if idx == len(self.matchers):
            return {}
        matcher = self.matchers[idx]
        for sym in list(self._remaining_symbols):
            bindings = matcher.matches(sym)
            if bindings is None:
                continue
            self._remaining_symbols.discard(sym)
            rest_bindings = self._matches_helper(idx + 1)
            self._remaining_symbols.add(sym)
            if rest_bindings is not None:
                # TODO: validate against dups?
                return bindings | rest_bindings
        return None

    def matches(self, sym: DataSymbol) -> Optional[Dict[str, Any]]:
        if self.relation == MatcherRelation.PARENTS:
            self._remaining_symbols = {
                par for par in sym.parents.keys() if not par.is_anonymous
            }
        elif self.relation == MatcherRelation.CHILDREN:
            self._remaining_symbols = {
                chd for chd in sym.childen.keys() if not chd.is_anonymous
            }
        else:
            raise ValueError("not implemented for relation type: %s" % self.relation)
        if self.exact and len(self._remaining_symbols) != len(self.matchers):
            return None
        return self._matches_helper(0)


class Children(_Relation):
    def __init__(
        self, bind_name: str, matchers: List[SymbolMatcher], exact: bool
    ) -> None:
        super().__init__(bind_name, MatcherRelation.CHILDREN, matchers, exact)


class Parents(_Relation):
    def __init__(
        self, bind_name: str, matchers: List[SymbolMatcher], exact: bool
    ) -> None:
        super().__init__(bind_name, MatcherRelation.PARENTS, matchers, exact)
