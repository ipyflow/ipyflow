# -*- coding: utf-8 -*-
import argparse
import shlex
from enum import Enum
from typing import TYPE_CHECKING, Any, List, NamedTuple, Optional

from ipyflow.tracing.output_recorder import CapturedIO

if TYPE_CHECKING:
    from ipyflow.data_model.symbol import Symbol
    from ipyflow.data_model.timestamp import Timestamp


_MEMOIZATION_PARSER = argparse.ArgumentParser("memoize")
_MEMOIZATION_PARSER.add_argument("-q", "--quiet", action="store_true")
_MEMOIZATION_PARSER.add_argument("-v", "--verbose", action="store_true")


def parse_verbosity(line: str) -> "MemoizedOutputLevel":
    args, _ = _MEMOIZATION_PARSER.parse_known_args(shlex.split(line))
    if args.quiet:
        return MemoizedOutputLevel.QUIET
    elif args.verbose:
        return MemoizedOutputLevel.VERBOSE
    else:
        return MemoizedOutputLevel.NORMAL


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
    inputs: List[MemoizedInput]
    outputs: List[MemoizedOutput]
    displayed_output: CapturedIO
    cell_ctr: int


class MemoizedOutputLevel(Enum):
    QUIET = "quiet"
    NORMAL = "normal"
    VERBOSE = "verbose"
