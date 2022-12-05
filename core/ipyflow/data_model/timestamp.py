# -*- coding: utf-8 -*-
import ast
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator, Iterable, NamedTuple, Optional, Union

from ipyflow.singletons import flow, tracer

if TYPE_CHECKING:
    # avoid circular imports
    from ipyflow.analysis.resolved_symbols import ResolvedDataSymbol
    from ipyflow.data_model.data_symbol import DataSymbol


logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


_cell_offset = 0
_stmt_offset = 0


class Timestamp(NamedTuple):
    cell_num: int
    stmt_num: int

    @classmethod
    def current(cls) -> "Timestamp":
        # TODO: shouldn't have to go through flow() singleton to get the cell counter,
        #  but the dependency structure prevents us from importing from ipyflow.data_model.code_cell
        return cls(
            flow().cell_counter() + _cell_offset,
            tracer().module_stmt_counter() + _stmt_offset,
        )

    @classmethod
    def uninitialized(cls) -> "Timestamp":
        return cls(-1, -1)

    @property
    def is_initialized(self) -> bool:
        return self > Timestamp.uninitialized()

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

    def __eq__(self, other) -> bool:
        if not isinstance(other, Timestamp):
            raise TypeError(
                "cannot compare non-timestamp value %s with timestamp %s"
                % (other, self)
            )
        return tuple(self._asdict().values()) == tuple(other._asdict().values())

    @classmethod
    def update_usage_info(
        cls,
        symbols: Union[
            Optional["DataSymbol"],
            Iterable[Optional["DataSymbol"]],
            Optional["ResolvedDataSymbol"],
            Iterable[Optional["ResolvedDataSymbol"]],
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
