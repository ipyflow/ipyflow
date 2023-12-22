# -*- coding: utf-8 -*-
import ast
import logging
from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    Generator,
    Iterable,
    NamedTuple,
    Optional,
    Tuple,
    Union,
)

from ipyflow.models import _TimestampContainer, cells, timestamps
from ipyflow.singletons import flow, tracer, tracer_initialized

if TYPE_CHECKING:
    # avoid circular imports
    from ipyflow.analysis.resolved_symbols import ResolvedSymbol
    from ipyflow.data_model.symbol import Symbol


logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


# just want to get rid of unused warning
_override_unused_warning_timestamps = timestamps


_cell_offset = 0
_stmt_offset = 0


class Timestamp(NamedTuple):
    cell_num: int
    stmt_num: int

    @classmethod
    def current(cls) -> "Timestamp":
        # TODO: shouldn't have to go through flow() singleton to get the cell counter,
        #  but the dependency structure prevents us from importing from ipyflow.data_model.code_cell
        if tracer_initialized():
            return cls(
                flow().cell_counter() + _cell_offset,
                tracer().module_stmt_counter() + _stmt_offset,
            )
        else:
            return Timestamp.uninitialized()

    @property
    def positional(self) -> "Timestamp":
        return Timestamp(cells().at_counter(self.cell_num).position, self.stmt_num)

    @classmethod
    def uninitialized(cls) -> "Timestamp":
        return _TS_UNINITIALIZED

    @property
    def is_initialized(self) -> bool:
        uninited = Timestamp.uninitialized()
        return self.cell_num > uninited.cell_num and self.stmt_num > uninited.stmt_num

    def plus(self, cell_num_delta: int, stmt_num_delta: int) -> "Timestamp":
        return self.__class__(
            self.cell_num + cell_num_delta, self.stmt_num + stmt_num_delta
        )

    @staticmethod
    @contextmanager
    def offset(
        cell_offset: int = 0, stmt_offset: int = 0
    ) -> Generator[None, None, None]:
        global _cell_offset
        global _stmt_offset
        _cell_offset += cell_offset
        _stmt_offset += stmt_offset
        try:
            yield
        finally:
            _cell_offset -= cell_offset
            _stmt_offset -= stmt_offset

    def as_tuple(self) -> Tuple[int, int]:
        return (self.cell_num, self.stmt_num)

    def __eq__(self, other) -> bool:
        if other is None:
            return False
        if not isinstance(other, Timestamp):
            raise TypeError(
                "cannot compare non-timestamp value %s with timestamp %s"
                % (other, self)
            )
        return tuple(self._asdict().values()) == tuple(other._asdict().values())

    def __ne__(self, other) -> bool:
        return not self == other

    @classmethod
    def update_usage_info(
        cls,
        symbols: Union[
            Optional["Symbol"],
            Iterable[Optional["Symbol"]],
            Optional["ResolvedSymbol"],
            Iterable[Optional["ResolvedSymbol"]],
        ],
        exclude_ns=False,
        used_node: Optional[ast.AST] = None,
    ):
        if symbols is None:
            return
        try:
            iter(symbols)  # type: ignore
        except TypeError:
            symbols = [symbols]  # type: ignore
        used_time = cls.current()
        for sym in symbols:  # type: ignore
            if sym is not None and not sym.is_anonymous:
                sym.update_usage_info(
                    used_time=used_time, used_node=used_node, exclude_ns=exclude_ns
                )


_TS_UNINITIALIZED = Timestamp(-1, -1)


if len(_TimestampContainer) == 0:
    _TimestampContainer.append(Timestamp)
else:
    _TimestampContainer[0] = Timestamp
