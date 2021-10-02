# -*- coding: future_annotations -*-
import ast
import logging
import re
import shlex
import subprocess
from typing import TYPE_CHECKING, NamedTuple

from nbsafety.analysis.live_refs import (
    compute_live_dead_symbol_refs,
    get_symbols_for_references,
    get_live_symbols_and_cells_for_references,
)
from nbsafety.ipython_utils import cell_counter as ipy_cell_counter
from nbsafety.singletons import nbs

if TYPE_CHECKING:
    from typing import Dict, Generator, Optional, Set
    from nbsafety.data_model.data_symbol import DataSymbol
    from nbsafety.types import CellId


logger = logging.getLogger(__name__)


_NB_MAGIC_PATTERN = re.compile(r'(^%|^!|^cd |\?$)')


class CheckerResult(NamedTuple):
    live: Set[DataSymbol]           # all live symbols in the cell
    deep_live: Set[DataSymbol]      # live symbols used in their entirety
    shallow_live: Set[DataSymbol]   # live symbols for which only a portion (attr or subscript) is used
    used_cells: Set[int]            # last updated timestamps of the live symbols
    live_cells: Set[int]            # cells that define a symbol that was called in the cell
    dead: Set[DataSymbol]           # symbols that are definitely assigned to
    stale: Set[DataSymbol]          # live symbols that have one or more ancestors with more recent timestamps
    typechecks: bool                # whether the cell typechecks successfully


class CodeCell:
    _current_cell_by_cell_id: Dict[CellId, CodeCell] = {}
    _cell_by_cell_ctr: Dict[int, CodeCell] = {}
    _cell_counter: int = 0

    def __init__(self, cell_id: CellId, cell_ctr: int, content: str) -> None:
        self.cell_id: CellId = cell_id
        self.cell_ctr: int = cell_ctr
        self.content: str = content
        self.needs_typecheck: bool = False
        self._checker_result: Optional[CheckerResult] = None

    def __str__(self):
        return self.content

    def __repr__(self):
        return f'<{self.__class__.__name__}[id={self.cell_id},ctr={self.cell_ctr}]>'

    def __hash__(self):
        return hash((self.cell_id, self.cell_ctr))

    @classmethod
    def create_and_track(
        cls, cell_id: CellId, content: str, validate_ipython_counter: bool = True
    ) -> CodeCell:
        cls._cell_counter += 1
        cell_ctr = cls._cell_counter
        if validate_ipython_counter:
            assert cell_ctr == ipy_cell_counter()
        cell = cls(cell_id, cell_ctr, content)
        cls._cell_by_cell_ctr[cell_ctr] = cell
        cur_cell = cls._current_cell_by_cell_id.get(cell_id, None)
        cur_cell_ctr = None if cur_cell is None else cur_cell.cell_ctr
        if cur_cell_ctr is None or cell_ctr > cur_cell_ctr:
            cls._current_cell_by_cell_id[cell_id] = cell
        return cell

    @classmethod
    def clear(cls):
        cls._current_cell_by_cell_id.clear()
        cls._cell_by_cell_ctr.clear()
        cls._cell_counter = 0

    @classmethod
    def exec_counter(cls) -> int:
        return cls._cell_counter

    @classmethod
    def next_exec_counter(cls) -> int:
        return cls.exec_counter() + 1

    @classmethod
    def all_run_cells(cls) -> Generator[CodeCell, None, None]:
        yield from cls._current_cell_by_cell_id.values()

    @classmethod
    def from_counter(cls, ctr: int) -> CodeCell:
        return cls._cell_by_cell_ctr[ctr]

    @classmethod
    def from_id(cls, cell_id: CellId) -> Optional[CodeCell]:
        return cls._current_cell_by_cell_id.get(cell_id, None)

    def sanitized_content(self):
        lines = []
        for line in self.content.strip().split('\n'):
            # TODO: figure out more robust strategy for filtering / transforming lines for the ast parser
            # we filter line magics, but for %time, we would ideally like to trace the statement being timed
            # TODO: how to do this?
            if _NB_MAGIC_PATTERN.search(line.strip()) is None:
                lines.append(line)
        return '\n'.join(lines)

    def ast(self) -> ast.Module:
        return ast.parse(self.sanitized_content())

    @property
    def is_current(self) -> bool:
        return self._current_cell_by_cell_id.get(self.cell_id, None) is self

    @classmethod
    def current_cell(cls) -> CodeCell:
        return cls._cell_by_cell_ctr[cls._cell_counter]

    def check_and_resolve_symbols(
        self, update_liveness_time_versions: bool = False
    ) -> CheckerResult:
        for dsym in nbs().live_symbols_by_cell_counter[self.cell_ctr]:
            dsym.timestamp_by_liveness_time_by_cell_counter[self.cell_ctr].clear()
        live_symbol_refs, dead_symbol_refs = compute_live_dead_symbol_refs(self.ast(), scope=nbs().global_scope)
        if update_liveness_time_versions:
            get_live_symbols_and_cells_for_references(
                live_symbol_refs, nbs().global_scope, self.cell_ctr, update_liveness_time_versions=True
            )
        deep_live_symbols, shallow_live_symbols, live_cells = get_live_symbols_and_cells_for_references(
            live_symbol_refs, nbs().global_scope, self.cell_ctr, update_liveness_time_versions=False
        )
        live_symbols = deep_live_symbols | shallow_live_symbols
        # only mark dead attrsubs as killed if we can traverse the entire chain
        dead_symbols, _ = get_symbols_for_references(
            dead_symbol_refs, nbs().global_scope, only_yield_successful_resolutions=True
        )
        stale_symbols = {dsym for dsym in live_symbols if dsym.is_stale}
        nbs().live_symbols_by_cell_counter[self.cell_ctr] = live_symbols
        if update_liveness_time_versions:
            for sym in live_symbols:
                nbs().cell_counter_by_live_symbol[sym].add(self.cell_ctr)
        self._checker_result = CheckerResult(
            live=live_symbols,
            deep_live=deep_live_symbols,
            shallow_live=shallow_live_symbols,
            used_cells={
                sym.timestamp.cell_num for sym in deep_live_symbols
            } | {
                sym.timestamp_excluding_ns_descendents.cell_num for sym in shallow_live_symbols
            },
            live_cells=live_cells,
            dead=dead_symbols,
            stale=stale_symbols,
            typechecks=self._typechecks()
        )
        return self._checker_result

    def _build_typecheck_slice(self) -> str:
        # TODO: typecheck statically-resolvable nested symbols too, not just top-level
        live_cell_counters = {self.cell_ctr}
        for live_cell_num in self._checker_result.live_cells:
            if CodeCell.from_counter(live_cell_num).is_current:
                live_cell_counters.add(live_cell_num)
        live_cells = [CodeCell.from_counter(ctr) for ctr in sorted(live_cell_counters)]
        top_level_symbols = {sym.get_top_level() for sym in self._checker_result.live}
        top_level_symbols.discard(None)
        return '{type_declarations}\n\n{content}'.format(
            type_declarations='\n'.join(f'{sym.name}: {sym.get_type_annotation_string()}' for sym in top_level_symbols),
            content='\n'.join(live_cell.sanitized_content() for live_cell in live_cells),
        )

    def _typechecks(self) -> bool:
        if not self.needs_typecheck:
            return True if self._checker_result is None else self._checker_result.typechecks
        self.needs_typecheck = False
        typecheck_slice = self._build_typecheck_slice()
        try:
            # TODO: parse the output in order to pass up to the user
            ret = subprocess.call(f"mypy -c {shlex.quote(typecheck_slice)}", shell=True)
            return ret == 0
        except Exception:
            logger.exception('Exception occurred during type checking')
            return True
