# -*- coding: utf-8 -*-
from typing import Any, List, cast

from ipyflow.data_model.data_symbol import DataSymbol
from ipyflow.data_model.timestamp import Timestamp


def lift(sym: Any) -> DataSymbol:
    """
    Given the programmatic usage of some symbol,
    look up the corresponding DataSymbol metadata.
    """
    # See the `argument` handler in ipyflow_tracer for the
    # actual implementation; this is just a stub that ensures
    # that handler was able to find something.
    if sym is None or not isinstance(sym, DataSymbol):
        raise ValueError("unable to lookup metadata for symbol")
    return cast(DataSymbol, sym)


def code(sym: Any) -> str:
    """
    Given the programmatic usage of some symbol,
    look up the corresponding code for that symbol.
    """
    # See the `argument` handler in ipyflow_tracer for the
    # actual implementation; this is just a stub that ensures
    # that handler was able to find something.
    if sym is None or not isinstance(sym, DataSymbol):
        raise ValueError("unable to lookup metadata for symbol")
    return cast(DataSymbol, sym).code()


def timestamp(sym: Any) -> Timestamp:
    """
    Given the programmatic usage of some symbol,
    look up the corresponding timestamp for that symbol.
    """
    # See the `argument` handler in ipyflow_tracer for the
    # actual implementation; this is just a stub that ensures
    # that handler was able to find something.
    if sym is None or not isinstance(sym, DataSymbol):
        raise ValueError("unable to lookup metadata for symbol")
    return cast(DataSymbol, sym).timestamp


def deps(sym: Any) -> List[DataSymbol]:
    """
    Given the programmatic usage of some symbol,
    look up the corresponding dependencies for that symbol.
    """
    # See the `argument` handler in ipyflow_tracer for the
    # actual implementation; this is just a stub that ensures
    # that handler was able to find something.
    if sym is None or not isinstance(sym, DataSymbol):
        raise ValueError("unable to lookup metadata for symbol")
    return [dep for dep in cast(DataSymbol, sym).parents.keys() if not dep.is_anonymous]


def users(sym: Any) -> List[DataSymbol]:
    """
    Given the programmatic usage of some symbol,
    look up the corresponding users of that symbol.
    """
    # See the `argument` handler in ipyflow_tracer for the
    # actual implementation; this is just a stub that ensures
    # that handler was able to find something.
    if sym is None or not isinstance(sym, DataSymbol):
        raise ValueError("unable to lookup metadata for symbol")
    return [
        child
        for child in cast(DataSymbol, sym).children.keys()
        if not child.is_anonymous
    ]
