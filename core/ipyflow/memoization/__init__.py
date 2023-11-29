# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING, Any, List, NamedTuple

if TYPE_CHECKING:
    from ipyflow.data_model.symbol import Symbol
    from ipyflow.data_model.timestamp import Timestamp


class MemoizedInput(NamedTuple):
    symbol: "Symbol"
    ts_at_execution: "Timestamp"
    comparable: Any


class MemoizedOutput(NamedTuple):
    symbol: "Symbol"
    ts_at_execution: "Timestamp"
    value: Any


class MemoizedCellExecution(NamedTuple):
    content_at_execution: str
    inputs: List[MemoizedInput]
    outputs: List[MemoizedOutput]
    cell_ctr: int
