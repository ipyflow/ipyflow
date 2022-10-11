# -*- coding: utf-8 -*-
from typing import Any, cast

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
