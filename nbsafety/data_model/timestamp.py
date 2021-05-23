# -*- coding: future_annotations -*-
from typing import NamedTuple

from nbsafety.singletons import nbs, tracer


class Timestamp(NamedTuple):
    cell_num: int
    stmt_num: int

    @classmethod
    def current(cls) -> Timestamp:
        return cls(nbs().cell_counter(), tracer().stmt_counter())
