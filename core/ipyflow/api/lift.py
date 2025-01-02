# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING, Any, List, Set, Union, cast

from ipyflow.data_model.symbol import Symbol
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.tracing.watchpoint import Watchpoints

if TYPE_CHECKING:
    from ipywidgets import HTML


def _validate(sym: Any) -> Symbol:
    if sym is None or not isinstance(sym, Symbol):
        raise ValueError("unable to lookup metadata for symbol")
    return cast(Symbol, sym)


def lift(sym: Any) -> Symbol:
    """
    Given the programmatic usage of some symbol,
    look up the corresponding Symbol metadata.
    """
    # See the `argument` handler in ipyflow_tracer for the
    # actual implementation; this is just a stub that ensures
    # that handler was able to find something.
    return _validate(sym)


def code(sym: Any, **kwargs: Any) -> Union["HTML", str]:
    """
    Given the programmatic usage of some symbol,
    look up the corresponding code for that symbol.
    """
    # See the `argument` handler in ipyflow_tracer for the
    # actual implementation; this is just a stub that ensures
    # that handler was able to find something.
    return _validate(sym).code(**kwargs)


def timestamp(sym: Any) -> Timestamp:
    """
    Given the programmatic usage of some symbol,
    look up the corresponding timestamp for that symbol.
    """
    # See the `argument` handler in ipyflow_tracer for the
    # actual implementation; this is just a stub that ensures
    # that handler was able to find something.
    return _validate(sym).timestamp


def deps(sym: Any) -> List[Symbol]:
    """
    Given the programmatic usage of some symbol,
    look up the corresponding dependencies for that symbol.
    """
    # See the `argument` handler in ipyflow_tracer for the
    # actual implementation; this is just a stub that ensures
    # that handler was able to find something.
    sym = _validate(sym)
    return [dep for dep in sym.parents.keys() if not dep.is_anonymous]


def users(sym: Any) -> List[Symbol]:
    """
    Given the programmatic usage of some symbol,
    look up the corresponding users of that symbol.
    """
    # See the `argument` handler in ipyflow_tracer for the
    # actual implementation; this is just a stub that ensures
    # that handler was able to find something.
    sym = _validate(sym)
    return [child for child in sym.children.keys() if not child.is_anonymous]


def mutate(sym: Any) -> None:
    """
    Force mutation for a particular symbol.
    """
    _validate(sym).mutate()


def _traverse(sym: Symbol, seen: Set[Symbol], attr: str) -> None:
    if sym in seen:
        return
    seen.add(sym)
    for related in getattr(sym, attr).keys():
        _traverse(related, seen, attr)


def rdeps(sym: Any) -> List[Symbol]:
    """
    Given the programmatic usage of some symbol, look up the
    corresponding recursive dependencies for that symbol.
    """
    # See the `argument` handler in ipyflow_tracer for the
    # actual implementation; this is just a stub that ensures
    # that handler was able to find something.
    sym = _validate(sym)
    seen: Set[Symbol] = set()
    _traverse(sym, seen, "parents")
    return [v for v in (seen - {sym}) if not v.is_anonymous]


def rusers(sym: Any) -> List[Symbol]:
    """
    Given the programmatic usage of some symbol,
    look up the corresponding users of that symbol.
    """
    # See the `argument` handler in ipyflow_tracer for the
    # actual implementation; this is just a stub that ensures
    # that handler was able to find something.
    sym = _validate(sym)
    seen: Set[Symbol] = set()
    _traverse(sym, seen, "children")
    ret = [v for v in (seen - {sym}) if not v.is_anonymous]
    return ret


def watchpoints(sym: Any) -> Watchpoints:
    """
    Given the programmatic usage of some symbol,
    look up the corresponding watchpoints for that symbol.
    """
    return _validate(sym).watchpoints


def set_tag(sym: Any, tag_value: str) -> None:
    """
    Add the tag `value` to the symbol.
    """
    _validate(sym).add_tag(tag_value)


def unset_tag(sym: Any, tag_value: str) -> None:
    """
    Remove the tag `value` from the symbol.
    """
    _validate(sym).remove_tag(tag_value)


def has_tag(sym: Any, tag_value: str) -> bool:
    """
    Test whether the symbol has the `value` tag.
    """
    return _validate(sym).has_tag(tag_value)
