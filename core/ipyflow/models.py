# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING, List, Optional, Type, Union, overload

from ipyflow.singletons import flow

if TYPE_CHECKING:
    from ipyflow.data_model.cell import Cell
    from ipyflow.data_model.namespace import Namespace
    from ipyflow.data_model.scope import Scope
    from ipyflow.data_model.statement import Statement
    from ipyflow.data_model.symbol import Symbol
    from ipyflow.data_model.timestamp import Timestamp
    from ipyflow.types import IdType


_CodeCellContainer: List[Type["Cell"]] = []
_NamespaceContainer: List[Type["Namespace"]] = []
_ScopeContainer: List[Type["Scope"]] = []
_StatementContainer: List[Type["Statement"]] = []
_SymbolContainer: List[Type["Symbol"]] = []
_TimestampContainer: List[Type["Timestamp"]] = []


if TYPE_CHECKING:

    @overload
    def cells(cell_id: None = None) -> Type["Cell"]: ...

    @overload
    def cells(cell_id: "IdType") -> "Cell": ...


def cells(cell_id: Optional["IdType"] = None) -> Union[Type["Cell"], "Cell"]:
    clazz = _CodeCellContainer[0]
    if cell_id is None:
        return clazz
    elif isinstance(cell_id, int) and cell_id <= clazz.exec_counter():
        return clazz.at_counter(cell_id)
    else:
        return clazz.from_id(cell_id)


def cell_above() -> Optional["Cell"]:
    active_cell_id = flow().active_cell_id
    assert active_cell_id is not None
    return cells().at_position(cells().from_id(active_cell_id).position - 1)


def cell_below() -> Optional["Cell"]:
    active_cell_id = flow().active_cell_id
    assert active_cell_id is not None
    return cells().at_position(cells().from_id(active_cell_id).position + 1)


def cell_at_offset(offset: int) -> Optional["Cell"]:
    active_cell_id = flow().active_cell_id
    assert active_cell_id is not None
    return cells().at_position(cells().from_id(active_cell_id).position + offset)


def last_run_cell() -> Optional["Cell"]:
    return cells().at_counter(cells().exec_counter() - 1)


def namespaces() -> Type["Namespace"]:
    return _NamespaceContainer[0]


def scopes() -> Type["Scope"]:
    return _ScopeContainer[0]


if TYPE_CHECKING:

    @overload
    def symbols(sym: None = None) -> Type["Symbol"]: ...

    @overload
    def symbols(sym: "Symbol") -> "Symbol": ...


def symbols(sym: Optional["Symbol"] = None) -> Union[Type["Symbol"], "Symbol"]:
    if sym is None:
        return _SymbolContainer[0]
    else:
        return sym


def statements() -> Type["Statement"]:
    return _StatementContainer[0]


def timestamps() -> Type["Timestamp"]:
    return _TimestampContainer[0]
