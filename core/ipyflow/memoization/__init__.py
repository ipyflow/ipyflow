# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING, Any, List, NamedTuple, Optional

if TYPE_CHECKING:
    from ipyflow.data_model.symbol import Symbol
    from ipyflow.data_model.timestamp import Timestamp


class MemoizedInput(NamedTuple):
    symbol: "Symbol"
    ts_at_execution: "Timestamp"
    mem_ts_at_execution: Optional["Timestamp"]
    obj_id_at_execution: int
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
