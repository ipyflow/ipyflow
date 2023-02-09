# -*- coding: utf-8 -*-
import ast
import logging
import re
import shlex
import subprocess
from collections import defaultdict
from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    Dict,
    FrozenSet,
    Generator,
    List,
    NamedTuple,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
    cast,
)

import pyccolo as pyc
from IPython import get_ipython

from ipyflow.analysis.live_refs import (
    LiveSymbolRef,
    SymbolRef,
    compute_live_dead_symbol_refs,
    get_live_symbols_and_cells_for_references,
    get_symbols_for_references,
)
from ipyflow.analysis.resolved_symbols import ResolvedDataSymbol
from ipyflow.analysis.slicing import CodeCellSlicingMixin
from ipyflow.config import ExecutionSchedule, FlowDirection
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.ipython_utils import _IPY, CapturedIO
from ipyflow.ipython_utils import cell_counter as ipy_cell_counter
from ipyflow.singletons import flow, kernel
from ipyflow.types import CellId, TimestampOrCounter

if TYPE_CHECKING:
    from ipyflow.data_model.data_symbol import DataSymbol


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class CheckerResult(NamedTuple):
    live: Set[ResolvedDataSymbol]  # all live symbols in the cell
    unresolved_live_refs: Set[LiveSymbolRef]  # any live symbol we couldn't resolve
    used_cells: Set[int]  # last updated timestamps of the live symbols
    live_cells: Set[int]  # cells that define a symbol that was called in the cell
    dead: Set["DataSymbol"]  # symbols that are definitely assigned to
    typechecks: bool  # whether the cell typechecks successfully


def cells() -> Type["CodeCell"]:
    return CodeCell


class CodeCell(CodeCellSlicingMixin):
    _current_cell_by_cell_id: Dict[CellId, "CodeCell"] = {}
    _cell_by_cell_ctr: Dict[int, "CodeCell"] = {}
    _cell_counter: int = 0
    _position_by_cell_id: Dict[CellId, int] = {}
    _cells_by_tag: Dict[str, Set["CodeCell"]] = defaultdict(set)
    _reactive_cells_by_tag: Dict[str, Set[CellId]] = defaultdict(set)
    _override_current_cell: Optional["CodeCell"] = None

    def __init__(
        self,
        cell_id: CellId,
        cell_ctr: int,
        content: str,
        tags: Tuple[str, ...],
        prev_cell: Optional["CodeCell"] = None,
    ) -> None:
        self.cell_id: CellId = cell_id
        self.cell_ctr: int = cell_ctr
        self.history: List[int] = [cell_ctr] if cell_ctr > -1 else []
        self.executed_content: str = content
        self.current_content: str = content
        self.last_ast_content: Optional[str] = None
        self.captured_output: Optional[CapturedIO] = None
        self.tags: Tuple[str, ...] = tags
        self.prev_cell = prev_cell
        self.override_live_refs: Optional[List[str]] = None
        self.override_dead_refs: Optional[List[str]] = None
        self.reactive_tags: Set[str] = set()
        self._dynamic_parents: Set[Tuple[CellId, "DataSymbol"]] = set()
        self._dynamic_children: Set[Tuple[CellId, "DataSymbol"]] = set()
        self._static_parents: Set[Tuple[CellId, "DataSymbol"]] = set()
        self._static_children: Set[Tuple[CellId, "DataSymbol"]] = set()
        self._used_cell_counters_by_live_symbol: Dict[
            "DataSymbol", Set[int]
        ] = defaultdict(set)
        self._cached_ast: Optional[ast.Module] = None
        self._cached_typecheck_result: Optional[bool] = (
            None if flow().settings.mark_typecheck_failures_unsafe else True
        )
        self._ready: bool = False
        self._extra_stmt: Optional[ast.stmt] = None

    @classmethod
    def clear(cls):
        cls._current_cell_by_cell_id = {}
        cls._cell_by_cell_ctr = {}
        cls._cell_counter = 0
        cls._position_by_cell_id = {}
        cls._cells_by_tag.clear()
        cls._reactive_cells_by_tag.clear()

    def __str__(self):
        return self.executed_content

    def __repr__(self):
        return f"<{self.__class__.__name__}[id={self.cell_id},ctr={self.cell_ctr}]>"

    def __hash__(self):
        return hash((self.cell_id, self.cell_ctr))

    def add_used_cell_counter(self, sym: "DataSymbol", ctr: int) -> None:
        if ctr > 0:
            self._used_cell_counters_by_live_symbol[sym].add(ctr)

    @property
    def is_executed(self) -> bool:
        return self.cell_ctr > -1

    @property
    def is_ready(self) -> bool:
        return self._ready

    def set_ready(self, new_ready: bool) -> bool:
        old_ready = self._ready
        self._ready = new_ready
        return old_ready

    def mark_as_reactive_for_tag(self, tag: str) -> None:
        self.reactive_tags.add(tag)
        self._reactive_cells_by_tag[tag].add(self.cell_id)

    @classmethod
    def get_reactive_ids_for_tag(cls, tag: str) -> Set[CellId]:
        return cls._reactive_cells_by_tag.get(tag, set())

    def add_dynamic_parent(
        self, parent: Union["CodeCell", CellId], sym: "DataSymbol"
    ) -> None:
        pid = parent.cell_id if isinstance(parent, CodeCell) else parent
        if pid in self._dynamic_children:
            return
        if pid == self.cell_id:
            # in this case, inherit the previous parents, if any
            if self.prev_cell is not None:
                for prev_pid, prev_sym in self.prev_cell._dynamic_parents:
                    if sym is prev_sym:
                        self._dynamic_parents.add((prev_pid, sym))
            return
        self._dynamic_parents.add((pid, sym))
        parent = self.from_id(pid)
        parent._dynamic_children.add((self.cell_id, sym))

    def add_static_parent(
        self, parent: Union["CodeCell", CellId], sym: "DataSymbol"
    ) -> None:
        pid = parent.cell_id if isinstance(parent, CodeCell) else parent
        if pid in self._static_children:
            return
        if pid == self.cell_id:
            # in this case, inherit the previous parents, if any
            if self.prev_cell is not None:
                for prev_pid, prev_sym in self.prev_cell._static_parents:
                    if sym is prev_sym:
                        self._static_parents.add((prev_pid, sym))
            return
        self._static_parents.add((pid, sym))
        parent = self.from_id(pid)
        parent._static_children.add((self.cell_id, sym))

    @property
    def dynamic_parents(self) -> FrozenSet[Tuple[CellId, "DataSymbol"]]:
        # trick to catch some mutations at typecheck time w/out runtime overhead
        parents = self._dynamic_parents
        if flow().mut_settings.flow_order == FlowDirection.IN_ORDER:
            parents = {
                (cell_id, syms)
                for cell_id, syms in parents
                if self.position > self.from_id(cell_id).position
            }
        return cast("FrozenSet[Tuple[CellId, DataSymbol]]", parents)

    @property
    def dynamic_children(self) -> FrozenSet[Tuple[CellId, "DataSymbol"]]:
        children = self._dynamic_children
        if flow().mut_settings.flow_order == FlowDirection.IN_ORDER:
            children = {
                (cell_id, syms)
                for cell_id, syms in children
                if self.position < self.from_id(cell_id).position
            }
        return cast("FrozenSet[Tuple[CellId, DataSymbol]]", children)

    @property
    def static_parents(self) -> FrozenSet[Tuple[CellId, "DataSymbol"]]:
        parents = self._static_parents
        if flow().mut_settings.flow_order == FlowDirection.IN_ORDER:
            parents = {
                (cell_id, syms)
                for cell_id, syms in parents
                if self.position > self.from_id(cell_id).position
            }
        return cast("FrozenSet[Tuple[CellId, DataSymbol]]", parents)

    @property
    def static_children(self) -> FrozenSet[Tuple[CellId, "DataSymbol"]]:
        children = self._static_children
        if flow().mut_settings.flow_order == FlowDirection.IN_ORDER:
            children = {
                (cell_id, syms)
                for cell_id, syms in children
                if self.position < self.from_id(cell_id).position
            }
        return cast("FrozenSet[Tuple[CellId, DataSymbol]]", children)

    @property
    def dynamic_parent_ids(self) -> FrozenSet[CellId]:
        # trick to catch some mutations at typecheck time w/out runtime overhead
        return cast("FrozenSet[CellId]", {pid for pid, _ in self._dynamic_parents})

    @property
    def dynamic_children_ids(self) -> FrozenSet[CellId]:
        return cast("FrozenSet[CellId]", {cid for cid, _ in self._dynamic_children})

    @property
    def static_parent_ids(self) -> FrozenSet[CellId]:
        return cast("FrozenSet[CellId]", {pid for pid, _ in self._static_parents})

    @property
    def static_children_ids(self) -> FrozenSet[CellId]:
        return cast("FrozenSet[CellId]", {cid for cid, _ in self._static_children})

    @classmethod
    def create_and_track(
        cls,
        cell_id: CellId,
        content: str,
        tags: Tuple[str, ...],
        bump_cell_counter: bool = True,
        validate_ipython_counter: bool = True,
    ) -> "CodeCell":
        if bump_cell_counter:
            cls._cell_counter += 1
            cell_ctr = cls._cell_counter
            if validate_ipython_counter and cell_ctr != ipy_cell_counter():
                actual_counter = get_ipython().execution_count
                logger.warning(
                    "mismatch between cell counter (%d) and saved ipython counter (%d)",
                    cell_ctr,
                    ipy_cell_counter(),
                )
                logger.warning("fixing up to actual counter of %d", actual_counter)
                cell_ctr = cls._cell_counter = _IPY.cell_counter = actual_counter
        else:
            cell_ctr = -1
        prev_cell = cls.from_id(cell_id)
        if cell_ctr == -1:
            assert prev_cell is None
        if prev_cell is not None:
            tags = tuple(set(tags) | set(prev_cell.tags))
        cell = cls(cell_id, cell_ctr, content, tags, prev_cell=prev_cell)
        if prev_cell is not None:
            cell.history = prev_cell.history + cell.history
            cell._dynamic_children = prev_cell._dynamic_children
            cell._static_children = prev_cell._static_children
            for tag in prev_cell.tags:
                cls._cells_by_tag[tag].discard(prev_cell)
            for tag in prev_cell.reactive_tags:
                cls._reactive_cells_by_tag[tag].discard(prev_cell.cell_id)
        for tag in tags:
            cls._cells_by_tag[tag].add(cell)
        if cell_ctr > -1:
            cls._cell_by_cell_ctr[cell_ctr] = cell
        prev_cell_ctr = None if prev_cell is None else prev_cell.cell_ctr
        if prev_cell_ctr is None or cell_ctr > prev_cell_ctr:
            cls._current_cell_by_cell_id[cell_id] = cell
        return cell

    @classmethod
    def set_cell_positions(cls, order_index_by_cell_id: Dict[CellId, int]):
        cls._position_by_cell_id = order_index_by_cell_id

    @classmethod
    def set_override_refs(
        cls,
        override_live_refs_by_cell_id: Dict[CellId, List[str]],
        override_dead_refs_by_cell_id: Dict[CellId, List[str]],
    ):
        for cell_id, override_live_refs in override_live_refs_by_cell_id.items():
            cell = cls.from_id(cell_id)
            cell.override_live_refs = override_live_refs
        for cell_id, override_dead_refs in override_dead_refs_by_cell_id.items():
            cell = cls.from_id(cell_id)
            cell.override_dead_refs = override_dead_refs

    @classmethod
    @contextmanager
    def _override_position_index_for_current_flow_semantics(
        cls,
    ) -> Generator[None, None, None]:
        orig_position_by_cell_id = cls._position_by_cell_id
        try:
            if flow().mut_settings.flow_order == FlowDirection.ANY_ORDER:
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
    def all_cells_most_recently_run_for_each_id(
        cls,
    ) -> Generator["CodeCell", None, None]:
        yield from cls._current_cell_by_cell_id.values()

    @classmethod
    def from_counter(cls, ctr: int) -> "CodeCell":
        return cls._cell_by_cell_ctr[ctr]

    @classmethod
    def from_timestamp(cls, ts: TimestampOrCounter) -> "CodeCell":
        if isinstance(ts, Timestamp):
            return cls.from_counter(ts.cell_num)
        else:
            return cls.from_counter(ts)

    @classmethod
    def from_id(cls, cell_id: CellId) -> Optional["CodeCell"]:
        return cls._current_cell_by_cell_id.get(cell_id, None)

    @classmethod
    def from_tag(cls, tag: str) -> Set["CodeCell"]:
        return cls._cells_by_tag.get(tag, set())

    def _rewriter_and_sanitized_content(self) -> Tuple[Optional[pyc.AstRewriter], str]:
        # we transform magics, but for %time, we would ideally like to trace the statement being timed
        # TODO: how to do this?
        content = get_ipython().transform_cell(self.current_content)
        ast_rewriter, syntax_augmenters = kernel().make_rewriter_and_syntax_augmenters()
        for aug in syntax_augmenters:
            content = aug(content)
        return ast_rewriter, content

    def sanitized_content(self) -> str:
        return self._rewriter_and_sanitized_content()[1]

    @contextmanager
    def override_current_cell(self):
        orig_override = self._override_current_cell
        try:
            self.__class__._override_current_cell = self
            yield
        finally:
            self.__class__._override_current_cell = orig_override

    def to_ast(self, override: Optional[ast.Module] = None) -> ast.Module:
        if override is not None:
            self._cached_ast = override
            return self._cached_ast
        if (
            self._cached_ast is None
            or len(self.last_ast_content) != len(self.current_content)
            or self.last_ast_content != self.current_content
        ):
            rewriter, content = self._rewriter_and_sanitized_content()
            self._cached_ast = ast.parse(content)
            self.last_ast_content = self.current_content
            if rewriter is not None:
                with self.override_current_cell():
                    rewriter.visit(self._cached_ast)
        return self._cached_ast

    @property
    def is_current_for_id(self) -> bool:
        return self._current_cell_by_cell_id.get(self.cell_id, None) is self

    @classmethod
    def current_cell(cls) -> "CodeCell":
        return cls._override_current_cell or cls._cell_by_cell_ctr[cls._cell_counter]

    def get_max_used_live_symbol_cell_counter(
        self, live_symbols: Set[ResolvedDataSymbol], filter_to_reactive: bool = False
    ) -> int:
        min_allowed_cell_position_by_symbol: Optional[Dict["DataSymbol", int]] = None
        flow_ = flow()
        if (
            flow_.mut_settings.exec_schedule
            == ExecutionSchedule.HYBRID_DAG_LIVENESS_BASED
            and flow_.mut_settings.flow_order == FlowDirection.IN_ORDER
        ):
            min_allowed_cell_position_by_symbol = {}
            for parents in (self.static_parents, self.dynamic_parents):
                for pid, dsym in parents:
                    min_allowed_cell_position_by_symbol[dsym] = max(
                        min_allowed_cell_position_by_symbol.get(dsym, -1),
                        self.from_id(pid).position,
                    )
        with self._override_position_index_for_current_flow_semantics():
            max_used_cell_ctr = -1
            this_cell_pos = self.position
            for sym in live_symbols:
                if sym.is_blocking:
                    continue
                if (
                    filter_to_reactive
                    and not sym.is_reactive
                    and not flow().is_updated_reactive(sym.dsym)
                ):
                    continue
                live_sym_updated_cell_ctr = sym.timestamp.cell_num
                if (
                    live_sym_updated_cell_ctr
                    in self._used_cell_counters_by_live_symbol.get(sym.dsym, set())
                ):
                    used_cell_position = self.from_timestamp(
                        live_sym_updated_cell_ctr
                    ).position
                    if this_cell_pos >= used_cell_position:
                        if (
                            min_allowed_cell_position_by_symbol is None
                            or used_cell_position
                            >= min_allowed_cell_position_by_symbol.get(
                                sym.dsym, cast(int, float("inf"))
                            )
                        ):
                            max_used_cell_ctr = max(
                                max_used_cell_ctr,
                                live_sym_updated_cell_ctr,
                                sym.dsym._override_ready_liveness_cell_num,
                            )
            return max_used_cell_ctr

    def _get_live_dead_symbol_refs(
        self, update_liveness_time_versions: bool
    ) -> Tuple[Set[LiveSymbolRef], Set[SymbolRef], bool]:
        live_symbol_refs: Set[LiveSymbolRef] = set()
        dead_symbol_refs: Set[SymbolRef] = set()
        if self.override_live_refs is None and self.override_dead_refs is None:
            live_symbol_refs, dead_symbol_refs = compute_live_dead_symbol_refs(
                self.to_ast(), scope=flow().global_scope
            )
        else:
            if self.override_live_refs is not None:
                live_symbol_refs = {
                    LiveSymbolRef.from_string(ref) for ref in self.override_live_refs
                }
            if self.override_dead_refs is not None:
                dead_symbol_refs = {
                    SymbolRef.from_string(ref) for ref in self.override_dead_refs
                }
            update_liveness_time_versions = False
        return live_symbol_refs, dead_symbol_refs, update_liveness_time_versions

    def check_and_resolve_symbols(
        self, update_liveness_time_versions: bool = False
    ) -> CheckerResult:
        (
            live_symbol_refs,
            dead_symbol_refs,
            update_liveness_time_versions,
        ) = self._get_live_dead_symbol_refs(update_liveness_time_versions)
        (
            live_resolved_symbols,
            live_cells,
            unresolved_live_refs,
        ) = get_live_symbols_and_cells_for_references(
            live_symbol_refs,
            flow().global_scope,
            self.cell_ctr,
            update_liveness_time_versions=update_liveness_time_versions,
        )
        # only mark dead attrsubs as killed if we can traverse the entire chain
        dead_symbols, _ = get_symbols_for_references(
            dead_symbol_refs, flow().global_scope
        )
        for resolved in live_resolved_symbols:
            if resolved.is_deep:
                resolved.dsym.cells_where_deep_live.add(self)
            else:
                resolved.dsym.cells_where_shallow_live.add(self)
            self.add_used_cell_counter(resolved.dsym, resolved.timestamp.cell_num)
        used_cells = {resolved.timestamp.cell_num for resolved in live_resolved_symbols}
        return CheckerResult(
            live=live_resolved_symbols,
            unresolved_live_refs=unresolved_live_refs,
            used_cells=used_cells,
            live_cells=live_cells,
            dead=dead_symbols,
            typechecks=self._typechecks(live_cells, live_resolved_symbols),
        )

    def compute_phantom_cell_info(self, used_cells: Set[int]) -> Dict[CellId, Set[int]]:
        used_cell_counters_by_cell_id = defaultdict(set)
        used_cell_counters_by_cell_id[self.cell_id].add(self.exec_counter())
        for cell_num in used_cells:
            used_cell_counters_by_cell_id[self.from_timestamp(cell_num).cell_id].add(
                cell_num
            )
        return {
            cell_id: cell_execs
            for cell_id, cell_execs in used_cell_counters_by_cell_id.items()
            if len(cell_execs) >= 2
        }

    def _build_typecheck_slice(
        self, live_cell_ctrs: Set[int], live_symbols: Set[ResolvedDataSymbol]
    ) -> str:
        # TODO: typecheck statically-resolvable nested symbols too, not just top-level
        live_cell_counters = {self.cell_ctr}
        for live_cell_num in live_cell_ctrs:
            if self.from_timestamp(live_cell_num).is_current_for_id:
                live_cell_counters.add(live_cell_num)
        live_cells = [self.from_timestamp(ctr) for ctr in sorted(live_cell_counters)]
        top_level_symbols = {sym.dsym.get_top_level() for sym in live_symbols}
        top_level_symbols.discard(None)
        return "{type_declarations}\n\n{content}".format(
            type_declarations="\n".join(
                f"{sym.name}: {sym.get_type_annotation_string()}"
                for sym in top_level_symbols
            ),
            content="\n".join(
                live_cell.sanitized_content() for live_cell in live_cells
            ),
        )

    def _typechecks(
        self, live_cell_ctrs: Set[int], live_symbols: Set[ResolvedDataSymbol]
    ) -> bool:
        if self._cached_typecheck_result is not None:
            return self._cached_typecheck_result
        if self.override_live_refs is not None:
            # assume it typechecks in this case
            return True
        typecheck_slice = self._build_typecheck_slice(live_cell_ctrs, live_symbols)
        try:
            # TODO: parse the output in order to pass up to the user
            ret = subprocess.call(f"mypy -c {shlex.quote(typecheck_slice)}", shell=True)
            self._cached_typecheck_result = ret == 0
        except Exception:
            logger.exception("Exception occurred during type checking")
            self._cached_typecheck_result = True
        return self._cached_typecheck_result

    @property
    def needs_typecheck(self):
        return self._cached_typecheck_result is None

    def invalidate_typecheck_result(self):
        self._cached_typecheck_result = None
