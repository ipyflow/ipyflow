# -*- coding: future_annotations -*-
import ast
import logging
import re
import shlex
import subprocess
from collections import defaultdict
from contextlib import contextmanager
from typing import TYPE_CHECKING, NamedTuple

from nbsafety.analysis.live_refs import (
    compute_live_dead_symbol_refs,
    get_symbols_for_references,
    get_live_symbols_and_cells_for_references,
)
from nbsafety.analysis.slicing import CodeCellSlicingMixin
from nbsafety.data_model.timestamp import Timestamp
from nbsafety.ipython_utils import cell_counter as ipy_cell_counter
from nbsafety.run_mode import FlowOrder
from nbsafety.singletons import nbs

if TYPE_CHECKING:
    from typing import Dict, Generator, List, Optional, Set, Type, Union
    from nbsafety.data_model.data_symbol import DataSymbol
    from nbsafety.types import CellId, TimestampOrCounter


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


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


def cells() -> Type[ExecutedCodeCell]:
    return ExecutedCodeCell


class ExecutedCodeCell(CodeCellSlicingMixin):
    _current_cell_by_cell_id: Dict[CellId, ExecutedCodeCell] = {}
    _cell_by_cell_ctr: Dict[int, ExecutedCodeCell] = {}
    _cell_counter: int = 0
    _position_by_cell_id: Dict[CellId, int] = {}

    def __init__(self, cell_id: CellId, cell_ctr: int, content: str) -> None:
        self.cell_id: CellId = cell_id
        self.cell_ctr: int = cell_ctr
        self.content: str = content
        self.dynamic_parents: Set[CellId] = set()
        self.dynamic_children: Set[CellId] = set()
        self.static_parents: Set[CellId] = set()
        self.static_children: Set[CellId] = set()
        self._used_cell_counters_by_live_symbol: Dict[DataSymbol, Set[int]] = defaultdict(set)
        self._cached_ast: Optional[ast.Module] = None
        self._cached_typecheck_result: Optional[bool] = None if nbs().settings.mark_typecheck_failures_unsafe else True
        self._fresh: bool = False

    def __str__(self):
        return self.content

    def __repr__(self):
        return f'<{self.__class__.__name__}[id={self.cell_id},ctr={self.cell_ctr}]>'

    def __hash__(self):
        return hash((self.cell_id, self.cell_ctr))

    def add_used_cell_counter(self, sym: DataSymbol, ctr: int) -> None:
        if ctr > 0:
            self._used_cell_counters_by_live_symbol[sym].add(ctr)

    def set_fresh(self, new_fresh: bool) -> bool:
        old_fresh = self._fresh
        self._fresh = new_fresh
        return old_fresh

    def add_dynamic_parent(self, parent: Union[ExecutedCodeCell, CellId]) -> None:
        pid = parent.cell_id if isinstance(parent, ExecutedCodeCell) else parent
        if pid == self.cell_id:
            return
        self.dynamic_parents.add(pid)
        self.from_id(pid).dynamic_children.add(self.cell_id)

    def add_static_parent(self, parent: Union[ExecutedCodeCell, CellId]) -> None:
        pid = parent.cell_id if isinstance(parent, ExecutedCodeCell) else parent
        if pid == self.cell_id:
            return
        self.static_parents.add(pid)
        self.from_id(pid).static_children.add(self.cell_id)

    @classmethod
    def create_and_track(cls, cell_id: CellId, content: str, validate_ipython_counter: bool = True) -> ExecutedCodeCell:
        cls._cell_counter += 1
        cell_ctr = cls._cell_counter
        if validate_ipython_counter:
            assert cell_ctr == ipy_cell_counter()
        prev_cell = cls.from_id(cell_id)
        cell = cls(cell_id, cell_ctr, content)
        if prev_cell is not None:
            cell.dynamic_children = prev_cell.dynamic_children
            cell.static_children = prev_cell.static_children
        cls._cell_by_cell_ctr[cell_ctr] = cell
        cur_cell = cls._current_cell_by_cell_id.get(cell_id, None)
        cur_cell_ctr = None if cur_cell is None else cur_cell.cell_ctr
        if cur_cell_ctr is None or cell_ctr > cur_cell_ctr:
            cls._current_cell_by_cell_id[cell_id] = cell
        return cell

    @classmethod
    def clear(cls):
        cls._current_cell_by_cell_id = {}
        cls._cell_by_cell_ctr = {}
        cls._cell_counter = 0
        cls._position_by_cell_id = {}

    @classmethod
    def set_cell_positions(cls, order_index_by_cell_id: Dict[CellId, int]):
        cls._position_by_cell_id = order_index_by_cell_id

    @classmethod
    @contextmanager
    def _override_position_index_for_current_flow_semantics(cls) -> Generator[None, None, None]:
        orig_position_by_cell_id = cls._position_by_cell_id
        try:
            if nbs().mut_settings.flow_order == FlowOrder.ANY_ORDER:
                cls.set_cell_positions({})
            yield
        finally:
            cls.set_cell_positions(orig_position_by_cell_id)

    @property
    def position(self) -> int:
        return self._position_by_cell_id.get(self.cell_id, -1)

    @classmethod
    def exec_counter(cls) -> int:
        return cls._cell_counter

    @classmethod
    def next_exec_counter(cls) -> int:
        return cls.exec_counter() + 1

    @classmethod
    def all_cells_most_recently_run_for_each_id(cls) -> Generator[ExecutedCodeCell, None, None]:
        yield from cls._current_cell_by_cell_id.values()

    @classmethod
    def from_timestamp(cls, ts: TimestampOrCounter) -> ExecutedCodeCell:
        if isinstance(ts, Timestamp):
            return cls._cell_by_cell_ctr[ts.cell_num]
        else:
            return cls._cell_by_cell_ctr[ts]

    @classmethod
    def from_id(cls, cell_id: CellId) -> Optional[ExecutedCodeCell]:
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

    def to_ast(self, override: Optional[ast.Module] = None) -> ast.Module:
        if override is not None:
            self._cached_ast = override
        if self._cached_ast is None:
            self._cached_ast = ast.parse(self.sanitized_content())
        return self._cached_ast

    @property
    def is_current_for_id(self) -> bool:
        return self._current_cell_by_cell_id.get(self.cell_id, None) is self

    @classmethod
    def current_cell(cls) -> ExecutedCodeCell:
        return cls._cell_by_cell_ctr[cls._cell_counter]

    def get_max_used_live_symbol_cell_counter(self, live_symbols: Set[DataSymbol]) -> int:
        with self._override_position_index_for_current_flow_semantics():
            max_used_cell_ctr = -1
            this_cell_pos = self.position
            for sym in live_symbols:
                for cell_ctr in self._used_cell_counters_by_live_symbol.get(sym, []):
                    if self.from_timestamp(cell_ctr).position <= this_cell_pos:
                        max_used_cell_ctr = max(max_used_cell_ctr, cell_ctr)
            return max_used_cell_ctr

    def check_and_resolve_symbols(
        self, update_liveness_time_versions: bool = False
    ) -> CheckerResult:
        live_symbol_refs, dead_symbol_refs = compute_live_dead_symbol_refs(self.to_ast(), scope=nbs().global_scope)
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
        for sym in deep_live_symbols:
            sym.cells_where_deep_live.add(self)
            self.add_used_cell_counter(sym, sym.timestamp.cell_num)
        for sym in shallow_live_symbols:
            sym.cells_where_shallow_live.add(self)
            self.add_used_cell_counter(sym, sym.timestamp_excluding_ns_descendents.cell_num)
        return CheckerResult(
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
            typechecks=self._typechecks(live_cells, live_symbols),
        )

    def compute_phantom_cell_info(self, used_cells: Set[int]) -> Dict[CellId, Set[int]]:
        used_cell_counters_by_cell_id = defaultdict(set)
        used_cell_counters_by_cell_id[self.cell_id].add(self.exec_counter())
        for cell_num in used_cells:
            used_cell_counters_by_cell_id[self.from_timestamp(cell_num).cell_id].add(cell_num)
        return {
            cell_id: cell_execs
            for cell_id, cell_execs in used_cell_counters_by_cell_id.items()
            if len(cell_execs) >= 2
        }

    def _build_typecheck_slice(self, live_cell_ctrs: Set[int], live_symbols: Set[DataSymbol]) -> str:
        # TODO: typecheck statically-resolvable nested symbols too, not just top-level
        live_cell_counters = {self.cell_ctr}
        for live_cell_num in live_cell_ctrs:
            if self.from_timestamp(live_cell_num).is_current_for_id:
                live_cell_counters.add(live_cell_num)
        live_cells = [self.from_timestamp(ctr) for ctr in sorted(live_cell_counters)]
        top_level_symbols = {sym.get_top_level() for sym in live_symbols}
        top_level_symbols.discard(None)
        return '{type_declarations}\n\n{content}'.format(
            type_declarations='\n'.join(f'{sym.name}: {sym.get_type_annotation_string()}' for sym in top_level_symbols),
            content='\n'.join(live_cell.sanitized_content() for live_cell in live_cells),
        )

    def _typechecks(self, live_cell_ctrs: Set[int], live_symbols: Set[DataSymbol]) -> bool:
        if self._cached_typecheck_result is not None:
            return self._cached_typecheck_result
        typecheck_slice = self._build_typecheck_slice(live_cell_ctrs, live_symbols)
        try:
            # TODO: parse the output in order to pass up to the user
            ret = subprocess.call(f"mypy -c {shlex.quote(typecheck_slice)}", shell=True)
            self._cached_typecheck_result = (ret == 0)
        except Exception:
            logger.exception('Exception occurred during type checking')
            self._cached_typecheck_result = True
        return self._cached_typecheck_result

    @property
    def needs_typecheck(self):
        return self._cached_typecheck_result is None

    def invalidate_typecheck_result(self):
        self._cached_typecheck_result = None
