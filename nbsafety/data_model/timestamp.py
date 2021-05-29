# -*- coding: future_annotations -*-
import logging
from typing import TYPE_CHECKING, NamedTuple

from nbsafety.singletons import nbs, tracer

if TYPE_CHECKING:
    from typing import Iterable, Optional, Union

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
    def update_usage_info(cls, symbols: Union[Optional[DataSymbol], Iterable[Optional[DataSymbol]]], exclude_ns=False):
        if symbols is None:
            return
        try:
            iter(symbols)  # type: ignore
        except TypeError:
            symbols = [symbols]  # type: ignore
        used_time = cls.current()
        for sym in symbols:  # type: ignore
            if sym is not None:
                sym.update_usage_info(used_time=used_time, exclude_ns=exclude_ns)
