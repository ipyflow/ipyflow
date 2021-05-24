# -*- coding: future_annotations -*-
import logging
from typing import NamedTuple

from nbsafety.singletons import nbs, tracer


logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


class Timestamp(NamedTuple):
    cell_num: int
    stmt_num: int

    @classmethod
    def current(cls) -> Timestamp:
        return cls(nbs().cell_counter(), tracer().stmt_counter())

    @classmethod
    def uninitialized(cls) -> Timestamp:
        return cls(-1, -1)

    @property
    def is_initialized(self):
        return self > Timestamp.uninitialized()

    def __eq__(self, other) -> bool:
        if not isinstance(other, Timestamp):
            raise TypeError("cannot compare non-timestamp value %s with timestamp %s" % (other, self))
        return tuple(self._asdict().values()) == tuple(other._asdict().values())
