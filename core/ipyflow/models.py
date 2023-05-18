# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING, List, Type

if TYPE_CHECKING:
    from ipyflow.data_model.code_cell import CodeCell
    from ipyflow.data_model.namespace import Namespace
    from ipyflow.data_model.scope import Scope
    from ipyflow.data_model.statement import Statement
    from ipyflow.data_model.symbol import Symbol
    from ipyflow.data_model.timestamp import Timestamp


_CodeCellContainer: List[Type["CodeCell"]] = []
_NamespaceContainer: List[Type["Namespace"]] = []
_ScopeContainer: List[Type["Scope"]] = []
_StatementContainer: List[Type["Statement"]] = []
_SymbolContainer: List[Type["Symbol"]] = []
_TimestampContainer: List[Type["Timestamp"]] = []


def cells() -> Type["CodeCell"]:
    return _CodeCellContainer[0]


def namespaces() -> Type["Namespace"]:
    return _NamespaceContainer[0]


def scopes() -> Type["Scope"]:
    return _ScopeContainer[0]


def symbols() -> Type["Symbol"]:
    return _SymbolContainer[0]


def statements() -> Type["Statement"]:
    return _StatementContainer[0]


def timestamps() -> Type["Timestamp"]:
    return _TimestampContainer[0]
