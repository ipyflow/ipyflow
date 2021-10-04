# -*- coding: future_annotations -*-
import ast
import astunparse
import logging
import re
import shlex
import subprocess
from collections import defaultdict
from typing import TYPE_CHECKING, NamedTuple

from nbsafety.analysis.live_refs import (
    compute_live_dead_symbol_refs,
    get_symbols_for_references,
    get_live_symbols_and_cells_for_references,
)
from nbsafety.data_model.timestamp import Timestamp
from nbsafety.ipython_utils import cell_counter as ipy_cell_counter
from nbsafety.singletons import nbs

if TYPE_CHECKING:
    from typing import Dict, Generator, List, Optional, Set, Type, TypeVar
    from nbsafety.data_model.data_symbol import DataSymbol
    from nbsafety.types import CellId
    TimestampOrCounter = TypeVar('TimestampOrCounter', Timestamp, int)


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


class ExecutedCodeCell:
    _current_cell_by_cell_id: Dict[CellId, ExecutedCodeCell] = {}
    _cell_by_cell_ctr: Dict[int, ExecutedCodeCell] = {}
    _cell_counter: int = 0
    _position_by_cell_id: Dict[CellId, int] = {}
    position_independent = True

    def __init__(self, cell_id: CellId, cell_ctr: int, content: str) -> None:
        self.cell_id: CellId = cell_id
        self.cell_ctr: int = cell_ctr
        self.content: str = content
        self._used_cell_counters_by_live_symbol: Dict[DataSymbol, Set[int]] = defaultdict(set)
        self._cached_ast: Optional[ast.Module] = None
        self._cached_typecheck_result: Optional[bool] = None if nbs().settings.mark_typecheck_failures_unsafe else True

    def __str__(self):
        return self.content

    def __repr__(self):
        return f'<{self.__class__.__name__}[id={self.cell_id},ctr={self.cell_ctr}]>'

    def __hash__(self):
        return hash((self.cell_id, self.cell_ctr))

    def add_used_cell_counter(self, sym: DataSymbol, ctr: int) -> None:
        if ctr > 0:
            self._used_cell_counters_by_live_symbol[sym].add(ctr)

    @classmethod
    def create_and_track(
        cls, cell_id: CellId, content: str, validate_ipython_counter: bool = True
    ) -> ExecutedCodeCell:
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
        cls._current_cell_by_cell_id = {}
        cls._cell_by_cell_ctr = {}
        cls._cell_counter = 0
        cls._position_by_cell_id = {}

    @classmethod
    def set_cell_positions(cls, order_index_by_cell_id: Optional[Dict[CellId, int]]):
        if order_index_by_cell_id is None:
            cls.position_independent = True
            cls._position_by_cell_id = {}
        else:
            cls.position_independent = False
            cls._position_by_cell_id = order_index_by_cell_id

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

    def compute_slice(self, stmt_level: bool = False) -> Dict[int, str]:
        """
        Gets a dictionary object of cell dependencies for the cell with
        the specified execution counter.

        Args:
            - cell_num (int): cell to get dependencies for, defaults to last
                execution counter

        Returns:
            - dict (int, str): map from required cell number to code
                representing dependencies
        """
        if stmt_level:
            stmts_by_cell_num = self.compute_slice_stmts()
            stmts_by_cell_num.pop(self.cell_ctr, None)
            ret = {
                ctr: '\n'.join(astunparse.unparse(stmt).strip() for stmt in stmts)
                for ctr, stmts in stmts_by_cell_num.items()
            }
            ret[self.cell_ctr] = self.content
            return ret
        else:
            deps: Set[int] = _compute_slice_impl(self.cell_ctr)
            return {dep: self.from_timestamp(dep).content for dep in deps}

    def compute_slice_stmts(self) -> Dict[int, List[ast.stmt]]:
        deps_stmt: Set[Timestamp] = _compute_slice_impl(Timestamp(self.cell_ctr, -1))
        stmts_by_cell_num = defaultdict(list)
        seen_stmt_ids = set()
        for ts in sorted(deps_stmt):
            if ts.cell_num > self.cell_ctr:
                break
            stmt = self.from_timestamp(ts.cell_num).to_ast().body[ts.stmt_num]
            stmt_id = id(stmt)
            if stmt is None or stmt_id in seen_stmt_ids:
                continue
            seen_stmt_ids.add(stmt_id)
            if stmt is not None:
                stmts_by_cell_num[ts.cell_num].append(stmt)
        stmts_by_cell_num[self.cell_ctr] = list(self.to_ast().body)
        return dict(stmts_by_cell_num)


def _compute_slice_impl(seed_ts: TimestampOrCounter) -> Set[TimestampOrCounter]:
    dependencies: Set[TimestampOrCounter] = set()
    timestamp_to_dynamic_ts_deps: Dict[TimestampOrCounter, Set[TimestampOrCounter]] = defaultdict(set)
    timestamp_to_static_ts_deps: Dict[TimestampOrCounter, Set[TimestampOrCounter]] = defaultdict(set)

    for sym in nbs().all_data_symbols():
        if nbs().mut_settings.dynamic_slicing_enabled:
            for used_time, sym_timestamp_when_used in sym.timestamp_by_used_time.items():
                if sym_timestamp_when_used < used_time:
                    if isinstance(seed_ts, Timestamp):
                        timestamp_to_dynamic_ts_deps[used_time].add(sym_timestamp_when_used)
                    else:
                        timestamp_to_dynamic_ts_deps[used_time.cell_num].add(sym_timestamp_when_used.cell_num)
        if nbs().mut_settings.static_slicing_enabled:
            for liveness_time, sym_timestamp_when_used in list(sym.timestamp_by_liveness_time.items()):
                if sym_timestamp_when_used < liveness_time:
                    if isinstance(seed_ts, Timestamp):
                        timestamp_to_static_ts_deps[liveness_time].add(sym_timestamp_when_used)
                    else:
                        timestamp_to_static_ts_deps[liveness_time.cell_num].add(
                            sym_timestamp_when_used.cell_num
                        )

    # ensure we at least get the static deps
    _get_ts_dependencies(
        seed_ts, dependencies, timestamp_to_dynamic_ts_deps, timestamp_to_static_ts_deps
    )
    if isinstance(seed_ts, Timestamp):
        for ts in list(timestamp_to_dynamic_ts_deps.keys() | timestamp_to_static_ts_deps.keys()):
            if ts.cell_num == seed_ts.cell_num:
                _get_ts_dependencies(
                    ts, dependencies, timestamp_to_dynamic_ts_deps, timestamp_to_static_ts_deps
                )
    return dependencies


def _get_ts_dependencies(
    timestamp: TimestampOrCounter,
    dependencies: Set[TimestampOrCounter],
    timestamp_to_dynamic_ts_deps: Dict[TimestampOrCounter, Set[TimestampOrCounter]],
    timestamp_to_static_ts_deps: Dict[TimestampOrCounter, Set[TimestampOrCounter]],
) -> None:
    """
    For a given timestamps, this function recursively populates a set of
    timestamps that the given timestamp depends on, based on the live symbols.

    Args:
        - timestamp (ts_or_int): current timestamp / cell to get dependencies for
        - dependencies (set<ts_or_int>): set of timestamps / cell coutners so far that exist
        - cell_num_to_dynamic_deps (dict<ts_or_int, set<ts_or_int>>): mapping from used timestamp
        to timestamp of symbol definition
        - timestamp_to_static_ts_deps (dict<ts_or_int, set<ts_or_int>>): mapping from used timestamp
        to timestamp of symbol definition (for statically computed timestamps)

    Returns:
        None
    """
    # Base case: cell already in dependencies
    if timestamp in dependencies:
        return
    if isinstance(timestamp, int) and timestamp < 0:
        return
    if isinstance(timestamp, Timestamp) and not timestamp.is_initialized:
        return

    # Add current cell to dependencies
    dependencies.add(timestamp)

    # Retrieve cell numbers for the dependent symbols
    # Add dynamic and static dependencies
    dep_timestamps = timestamp_to_dynamic_ts_deps[timestamp]
    logger.info('dynamic ts deps for %s: %s', timestamp, dep_timestamps)
    static_ts_deps = timestamp_to_static_ts_deps[timestamp]
    dep_timestamps |= static_ts_deps
    logger.info('static ts deps for %s: %s', timestamp, static_ts_deps)

    # For each dependent cell, recursively get their dependencies
    for ts in dep_timestamps - dependencies:
        _get_ts_dependencies(
            ts, dependencies, timestamp_to_dynamic_ts_deps, timestamp_to_static_ts_deps
        )
