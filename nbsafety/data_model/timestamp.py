# -*- coding: future_annotations -*-
import logging
from typing import TYPE_CHECKING, NamedTuple

from nbsafety.singletons import nbs, tracer

if TYPE_CHECKING:
    from typing import Optional, Set, Union

    # avoid circular imports
    from nbsafety.data_model.data_symbol import DataSymbol


logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


class Timestamp(NamedTuple):
    cell_num: int
    stmt_num: int

    @classmethod
    def current(cls) -> Timestamp:
        return cls(nbs().cell_counter(), tracer().module_stmt_counter())

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

    @classmethod
    def update_usage_info(cls, symbols: Union[Optional[DataSymbol], Set[Optional[DataSymbol]]], exclude_ns=False):
        used_time = cls.current()
        for sym in (symbols if symbols is not None and isinstance(symbols, set) else [symbols]):
            if sym is None:
                continue
            if nbs().is_develop:
                logger.info('sym `%s` used in cell %d last updated in cell %d', sym, used_time.cell_num, sym.timestamp)
            if used_time not in sym.timestamp_by_used_time and sym.timestamp < used_time:
                sym.timestamp_by_used_time[used_time] = (
                    sym.timestamp_excluding_ns_descendents if exclude_ns else sym.timestamp
                )
