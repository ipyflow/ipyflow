# -*- coding: utf-8 -*-
"""
Tools describing how non-notebook code affects dataflow
"""


def handler_for(*methods):
    """
    Just a marker decorator to indicate that the handler is used for functions / methods
    named differently from the decorated function / method
    """
    pass


def module(module):
    """
    Just a marker decorator indicating that the class / function belongs to the
    module indicated in the parameter, as opposed to the one indicated by the file name
    of the containing .pyi file.
    """
    pass


class SideEffect:
    """
    Stub for return annotation that indicates a side effect. Side effects
    can be comprised of a single action or multiple side effects.
    """


class SymbolUpserted(SideEffect):
    """
    Stub for indicating that a value is upserted as a side effect.
    """


class Mutated(SideEffect):
    """
    Stub for indicating that a value is mutated as a side effect.
    """

    pass


class Handler(SideEffect):
    """
    Stub for referencing more complex handlers than what can
    be represented just by annotations. To use, reference
    the handler in the return value annotation.

    Example:
        from ipyflow.tracing.mutation_event import ListAppend
        ...
        class list:
            def append(self, value) -> Handler[ListAppend]:
                ...
    """

    pass


class SymbolMatcher:
    """
    Indicates that the annotation matches a symbol
    """


class AllOf(SymbolMatcher):
    """
    Indicates all of the subscripted matchers should match.
    """


class AnyOf(SymbolMatcher):
    """
    Indicates that at least one of the subscripted matchers should match.
    If multiple match, the first wins.
    """


class Display(SymbolMatcher):
    """
    Stub for referencing that a value represents stdout / stderr / display contents.
    """

    pass


class FileSystem(SymbolMatcher):
    """
    Stub for referencing that a value represents file system contents.
    """

    pass


class Children(SymbolMatcher):
    """
    Stub for referencing that an argument's subscripted values depend on it.
    """

    pass


class Parents(SymbolMatcher):
    """
    Stub for referencing that an argument depends on the subscripted values.
    """

    pass
