# -*- coding: utf-8 -*-
import ast
import inspect
import logging
import shlex
import subprocess
from collections import defaultdict
from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    FrozenSet,
    Generator,
    Iterable,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Set,
    Tuple,
    Type,
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
from ipyflow.analysis.resolved_symbols import ResolvedSymbol
from ipyflow.config import ExecutionSchedule, FlowDirection, Interface
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.memoization import (
    MemoizedCellExecution,
    MemoizedInput,
    MemoizedOutput,
    MemoizedOutputLevel,
    parse_verbosity,
)
from ipyflow.models import _CodeCellContainer, cells, statements, symbols
from ipyflow.singletons import flow, shell
from ipyflow.slicing.mixin import FormatType, Slice, SliceableMixin
from ipyflow.tracing.output_recorder import IPyflowCapturedIO
from ipyflow.types import IdType, TimestampOrCounter
from ipyflow.utils.ipython_utils import _IPY
from ipyflow.utils.ipython_utils import cell_counter as ipy_cell_counter

if TYPE_CHECKING:
    from ipyflow.data_model.statement import Statement
    from ipyflow.data_model.symbol import Symbol


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


# just want to get rid of unused warning
_override_unused_warning_cells = cells


class CheckerResult(NamedTuple):
    live: Set[ResolvedSymbol]  # all live symbols in the cell
    unresolved_live_refs: Set[LiveSymbolRef]  # any live symbol we couldn't resolve
    used_cells: Set[int]  # last updated timestamps of the live symbols
    live_cells: Set[int]  # cells that define a symbol that was called in the cell
    dead: Set["Symbol"]  # symbols that are definitely assigned to
    modified: Set["Symbol"]  # symbols that are dead or modified
    typechecks: bool  # whether the cell typechecks successfully


class Cell(SliceableMixin):
    _current_cell_by_cell_id: Dict[IdType, "Cell"] = {}
    _cell_by_cell_ctr: Dict[int, "Cell"] = {}
    _cell_counter: int = 0
    _position_by_cell_id: Dict[IdType, int] = {}
    _cell_id_by_position: Dict[int, IdType] = {}
    _cells_by_tag: Dict[str, Set["Cell"]] = defaultdict(set)
    _reactive_cells_by_tag: Dict[str, Set[IdType]] = defaultdict(set)
    _override_current_cell: Optional["Cell"] = None
    _memoized_executions: Dict[str, Dict[int, MemoizedCellExecution]] = {}

    def __init__(
        self,
        cell_id: IdType,
        cell_ctr: int,
        content: str,
        tags: Tuple[str, ...],
        prev_cell: Optional["Cell"] = None,
        placeholder_id: bool = False,
        memoized_output_level: Optional[MemoizedOutputLevel] = None,
    ) -> None:
        self.cell_id: IdType = cell_id
        self.cell_ctr: int = cell_ctr
        self.last_check_content: Optional[str] = None
        self.last_check_cell_ctr: Optional[int] = None
        self.last_check_result: Optional[CheckerResult] = None
        self.error_in_exec: Optional[BaseException] = None
        self.history: List[int] = [cell_ctr] if cell_ctr > -1 else []
        self.executed_content: Optional[str] = None
        self.current_content: str = content
        self.last_ast_content: Optional[str] = None
        self.captured_output: Optional[IPyflowCapturedIO] = None
        self.tags: Tuple[str, ...] = tags
        self.prev_cell = prev_cell
        self.override_live_refs: Optional[List[str]] = None
        self.override_dead_refs: Optional[List[str]] = None
        self.reactive_tags: Set[str] = set()
        self.raw_dynamic_parents: Dict[IdType, Set["Symbol"]] = {}
        self.raw_dynamic_children: Dict[IdType, Set["Symbol"]] = {}
        self.raw_static_parents: Dict[IdType, Set["Symbol"]] = {}
        self.raw_static_children: Dict[IdType, Set["Symbol"]] = {}
        self.used_symbols: Set["Symbol"] = set()
        self.static_removed_symbols: Set["Symbol"] = set()
        self.static_writes: Set["Symbol"] = set()
        self.dynamic_writes: Set["Symbol"] = set()
        # pending dynamic writes are not finalized until the stmt is done executing
        self._pending_dynamic_writes: Set["Symbol"] = set()
        self._used_cell_counters_by_live_symbol: Dict["Symbol", Set[int]] = defaultdict(
            set
        )
        self._cached_ast: Optional[ast.Module] = None
        self._cached_typecheck_result: Optional[bool] = (
            None if flow().settings.mark_typecheck_failures_unsafe else True
        )
        self._ready: bool = False
        self._extra_stmt: Optional[ast.stmt] = None
        self._placeholder_id = placeholder_id
        self.memoized_output_level = memoized_output_level
        self.skipped_due_to_memoization_ctr = -1

    @property
    def id(self) -> IdType:
        return self.cell_id

    @property
    def is_error(self) -> bool:
        return self.error_in_exec is not None

    @property
    def is_dirty(self) -> bool:
        return self.current_content != self.executed_content

    @property
    def is_memoized(self) -> bool:
        return self.memoized_output_level is not None

    @property
    def timestamp(self) -> Timestamp:
        return Timestamp(self.cell_ctr, -1)

    @property
    def prev(self) -> Optional["Cell"]:
        return self.prev_cell

    @property
    def text(self) -> str:
        return self.sanitized_content().strip()

    @property
    def is_placeholder_id(self) -> bool:
        return self._placeholder_id

    @property
    def is_visible(self) -> bool:
        if flow().mut_settings.interface in (Interface.IPYTHON, Interface.UNKNOWN):
            return True
        return self.position not in (-1, float("inf"))

    @property
    def position(self) -> int:
        pos = self._position_by_cell_id.get(self.cell_id, -1)
        if pos == -1:
            settings = flow().mut_settings
            if (
                settings.flow_order == FlowDirection.IN_ORDER
                and settings.interface == Interface.IPYTHON
            ):
                assert isinstance(self.cell_id, int)
                pos = self.cell_id
        return pos

    @property
    def directional_parents(self) -> Mapping[IdType, FrozenSet["Symbol"]]:
        # trick to catch some mutations at typecheck time w/out runtime overhead
        parents = self.raw_parents
        if flow().mut_settings.flow_order == FlowDirection.IN_ORDER:
            parents = {
                sid: syms
                for sid, syms in parents.items()
                if self.position > self.from_id(sid).position
            }
        return cast("Mapping[IdType, FrozenSet[Symbol]]", parents)

    @property
    def directional_children(self) -> Mapping[IdType, FrozenSet["Symbol"]]:
        children = self.raw_children
        if flow().mut_settings.flow_order == FlowDirection.IN_ORDER:
            children = {
                cell_id: syms
                for cell_id, syms in children.items()
                if self.position < self.from_id(cell_id).position
            }
        return cast("Mapping[IdType, FrozenSet[Symbol]]", children)

    def get_latest_parent_by_ts_map(self) -> Optional[Dict[Timestamp, "Cell"]]:
        flow_ = flow()
        if flow_.mut_settings.flow_order != FlowDirection.IN_ORDER:
            return None
        latest_par_by_ts: Dict[Timestamp, "Cell"] = {}
        for _ in flow_.mut_settings.iter_slicing_contexts():
            for par_id, raw_syms in self.directional_parents.items():
                syms = raw_syms - self.static_removed_symbols - {flow_.fake_edge_sym}
                parent = cells().from_id(par_id)
                for sym in syms:
                    if (
                        parent.position
                        >= latest_par_by_ts.get(sym.shallow_timestamp, parent).position
                    ):
                        latest_par_by_ts[sym.shallow_timestamp] = parent
        return latest_par_by_ts

    def statements(self) -> List["Statement"]:
        stmts: List["Statement"] = []
        for stmt_num in range(len(self.to_ast().body)):
            stmts.append(statements().from_timestamp(self.cell_ctr, stmt_num))  # type: ignore
        return stmts

    @classmethod
    def clear(cls):
        cls._current_cell_by_cell_id = {}
        cls._cell_by_cell_ctr = {}
        cls._cell_counter = 0
        cls._position_by_cell_id = {}
        cls._cells_by_tag.clear()
        cls._reactive_cells_by_tag.clear()

    @classmethod
    def with_placeholder_ids(cls):
        return sorted(
            (
                cell
                for cell in cls._current_cell_by_cell_id.values()
                if cell._placeholder_id
            ),
            key=lambda cell: cell.cell_ctr,
        )

    def __str__(self):
        return self.executed_content

    def __repr__(self):
        return f"<{self.__class__.__name__}[ctr={self.cell_ctr},id={self.cell_id}]>"

    def __hash__(self):
        return hash((self.cell_id, self.cell_ctr))

    def update_id(self, new_id: IdType, update_edges: bool = True) -> None:
        old_id = self.cell_id
        self.cell_id = new_id
        self._placeholder_id = False
        if self.prev_cell is not None:
            self.prev_cell.update_id(new_id, update_edges=False)
        if not update_edges:
            return
        pos = self._position_by_cell_id.pop(old_id, None)
        if pos is not None:
            self._position_by_cell_id[new_id] = pos
        current_cell = self._current_cell_by_cell_id.pop(old_id, None)
        if current_cell is not None:
            assert current_cell is self
            self._current_cell_by_cell_id[new_id] = current_cell
        for reactive_cells in self._reactive_cells_by_tag.values():
            if old_id in reactive_cells:
                reactive_cells.discard(old_id)
                reactive_cells.add(new_id)
        for _ in flow().mut_settings.iter_slicing_contexts():
            for pid in self.raw_parents.keys():
                parent = self.from_id(pid)
                parent.raw_children = {
                    (new_id if cid == old_id else cid): syms
                    for cid, syms in parent.raw_children.items()
                }
            for cid in self.raw_children.keys():
                child = self.from_id(cid)
                child.raw_parents = {
                    (new_id if pid == old_id else pid): syms
                    for pid, syms in child.raw_parents.items()
                }

    def add_used_cell_counter(self, sym: "Symbol", ctr: int) -> None:
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
    def get_reactive_ids_for_tag(cls, tag: str) -> Set[IdType]:
        return cls._reactive_cells_by_tag.get(tag, set())

    def _maybe_memoize_params(self) -> None:
        if self.is_error:
            return
        inputs: Dict["Symbol", MemoizedInput] = {}
        for _ in flow().mut_settings.iter_slicing_contexts():
            for edges in self.raw_parents.values():
                for sym in edges:
                    if sym in inputs:
                        continue
                    sym_ts = sym.timestamp
                    if sym_ts.cell_num == self.cell_ctr:
                        continue
                    elif (
                        not sym.is_user_accessible or not sym.containing_scope.is_global
                    ):
                        continue
                    elif sym_ts.cell_num > self.cell_ctr:
                        return
                    inputs[sym] = MemoizedInput(
                        sym,
                        sym_ts,
                        sym.memoize_timestamp,
                        sym.obj_id,
                        sym.make_memoize_comparable()[0],
                    )
        outputs: Dict["Symbol", MemoizedOutput] = {}
        for sym in flow().updated_symbols:
            sym.last_updated_timestamp_by_obj_id[sym.obj_id] = sym.timestamp
            if not sym.is_user_accessible or not sym.containing_scope.is_global:
                continue
            outputs[sym] = MemoizedOutput(sym, sym.shallow_timestamp, sym.obj)
        assert self.captured_output is not None
        assert self.executed_content is not None
        self._memoized_executions.setdefault(self.executed_content, {})[
            self.cell_ctr
        ] = MemoizedCellExecution(
            list(inputs.values()),
            list(outputs.values()),
            self.captured_output,
            self.cell_ctr,
        )

    @classmethod
    def create_and_track(
        cls,
        cell_id: IdType,
        content: str,
        tags: Tuple[str, ...],
        bump_cell_counter: bool = True,
        validate_ipython_counter: bool = True,
        placeholder_id: bool = False,
        memoized_output_level: Optional[MemoizedOutputLevel] = None,
    ) -> "Cell":
        if bump_cell_counter:
            cls._cell_counter += 1
            cell_ctr = cls._cell_counter
            if validate_ipython_counter and cell_ctr != ipy_cell_counter():
                actual_counter = get_ipython().execution_count
                if flow().is_dev_mode:
                    logger.warning(
                        "mismatch between cell counter (%d) and saved ipython counter (%d)",
                        cell_ctr,
                        ipy_cell_counter(),
                    )
                    logger.warning("fixing up to actual counter of %d", actual_counter)
                cell_ctr = cls._cell_counter = _IPY.cell_counter = actual_counter
        else:
            cell_ctr = -1
        prev_cell = cls.from_id_nullable(cell_id)
        if cell_ctr == -1:
            assert prev_cell is None
        if prev_cell is not None:
            tags = tuple(set(tags) | set(prev_cell.tags))
        cell = cls(
            cell_id,
            cell_ctr,
            content,
            tags,
            prev_cell=prev_cell,
            placeholder_id=placeholder_id,
            memoized_output_level=memoized_output_level,
        )
        if prev_cell is not None:
            cell.history = prev_cell.history + cell.history
            cell.raw_static_children = prev_cell.raw_static_children
            cell.raw_dynamic_children = prev_cell.raw_dynamic_children
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
    def set_cell_positions(cls, order_index_by_cell_id: Dict[IdType, int]):
        settings = flow().mut_settings
        if (
            settings.flow_order == FlowDirection.IN_ORDER
            and settings.interface != Interface.IPYTHON
        ):
            cls._cell_id_by_position.clear()
            for cell_id in cls._position_by_cell_id:
                if cell_id not in order_index_by_cell_id:
                    order_index_by_cell_id[cell_id] = cast(int, float("inf"))
                else:
                    cls._cell_id_by_position[order_index_by_cell_id[cell_id]] = cell_id
        cls._position_by_cell_id = order_index_by_cell_id

    @classmethod
    def iterate_over_notebook_in_position_order(cls) -> Generator["Cell", None, None]:
        for pos in sorted(cls._cell_id_by_position.keys()):
            yield cls.from_id(cls._cell_id_by_position[pos])

    @classmethod
    def iterate_over_notebook_in_counter_order(cls) -> Generator["Cell", None, None]:
        yield from sorted(
            cls._current_cell_by_cell_id.values(), key=lambda cell: cell.cell_ctr
        )

    @classmethod
    def set_override_refs(
        cls,
        override_live_refs_by_cell_id: Dict[IdType, List[str]],
        override_dead_refs_by_cell_id: Dict[IdType, List[str]],
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

    @classmethod
    def exec_counter(cls) -> int:
        return cls._cell_counter

    @classmethod
    def next_exec_counter(cls) -> int:
        return cls.exec_counter() + 1

    @classmethod
    def current_cells_for_each_id(cls) -> Generator["Cell", None, None]:
        yield from cls._current_cell_by_cell_id.values()

    @classmethod
    def all_executed_cell_ids(cls) -> Generator[IdType, None, None]:
        for cell in cls._cell_by_cell_ctr.values():
            if cell.cell_ctr > 0:
                yield cell.cell_id

    @classmethod
    def at_counter(cls, ctr: int) -> "Cell":
        return cls._cell_by_cell_ctr[ctr]

    @classmethod
    def from_counter(cls, ctr: int) -> "Cell":
        return cls.at_counter(ctr)

    @classmethod
    def at_position(cls, pos: int) -> Optional["Cell"]:
        for cell_id, cell_pos in cls._position_by_cell_id.items():
            if cell_pos == pos:
                return cls.from_id(cell_id)
        return None

    @classmethod
    def from_position(cls, pos: int) -> Optional["Cell"]:
        return cls.at_position(pos)

    @classmethod
    def at_timestamp(
        cls, ts: TimestampOrCounter, stmt_num: Optional[int] = None
    ) -> "Cell":
        assert stmt_num is None
        if isinstance(ts, Timestamp):
            return cls.at_counter(ts.cell_num)
        else:
            return cls.at_counter(ts)

    @classmethod
    def from_id(cls, cell_id: IdType) -> "Cell":
        return cls._current_cell_by_cell_id[cell_id]

    @classmethod
    def from_id_nullable(cls, cell_id: IdType) -> Optional["Cell"]:
        return cls._current_cell_by_cell_id.get(cell_id)

    @classmethod
    def has_id(cls, cell_id: IdType):
        return cell_id in cls._current_cell_by_cell_id

    @classmethod
    def from_tag(cls, tag: str) -> Set["Cell"]:
        return cls._cells_by_tag.get(tag, set())

    @staticmethod
    def get_memoized_content_and_output_level(
        content: str,
    ) -> Tuple[Optional[str], Optional[MemoizedOutputLevel]]:
        cell_lines = content.strip().splitlines(keepends=True)
        if len(cell_lines) == 0:
            return None, None
        first_line = cell_lines[0].lstrip()
        memoize_magic = r"%%memoize"
        if not first_line.startswith(memoize_magic):
            return None, None
        return "".join(cell_lines[1:]), parse_verbosity(
            first_line[len(memoize_magic) :].strip()
        )

    @classmethod
    def get_memoized_content(cls, content: str) -> Optional[str]:
        return cls.get_memoized_content_and_output_level(content)[0]

    @classmethod
    def get_memoized_output_level(cls, content: str) -> Optional[MemoizedOutputLevel]:
        return cls.get_memoized_content_and_output_level(content)[1]

    @property
    def raw_cell(self) -> str:
        return self.get_memoized_content(self.current_content) or self.current_content

    @property
    def transformed_cell(self) -> str:
        cell = self.get_transformed_memoized_content()
        if cell is not None:
            return cell
        return self.raw_and_sanitized_content()[1]

    def get_memoized_counter(self) -> Optional[int]:
        prev_cell = self.prev_cell
        if not self.is_memoized or prev_cell is None:
            return None

        symbols_ = symbols()
        for (
            inputs,
            outputs,
            displayed_output,
            ctr,
        ) in prev_cell._memoized_executions.get(
            self.executed_content or "", {}
        ).values():
            if ctr >= self.cell_ctr:
                continue
            for sym, in_ts, mem_ts, obj_id, comparable in inputs:
                if comparable is not symbols_.NULL:
                    # prefer the comparable check if it is available
                    current_comp, eq = sym.make_memoize_comparable()
                    if current_comp is symbols_.NULL or eq is None:
                        break
                    if eq(current_comp, comparable):
                        continue
                    else:
                        break
                if sym.is_import or sym.timestamp.cell_num == in_ts.cell_num:
                    continue
                elif sym.obj_id == obj_id and sym.memoize_timestamp in (
                    in_ts,
                    mem_ts or Timestamp.uninitialized(),
                ):
                    continue
                else:
                    break
            else:
                return ctr
        return None

    def get_transformed_memoized_content(
        self, ctr: Optional[int] = None
    ) -> Optional[str]:
        if ctr is None:
            ctr = self.get_memoized_counter()
        if ctr is None:
            return None
        if self.memoized_output_level == MemoizedOutputLevel.QUIET:
            return "pass"
        else:
            return f"Out.get({ctr})"

    def _rewriter_and_sanitized_content(
        self, raw_cell: Optional[str] = None, path: Optional[str] = None
    ) -> Tuple[Optional[pyc.AstRewriter], str]:
        # we transform magics, but for %time, we would ideally like to trace the statement being timed
        # TODO: how to do this?
        shell_ = shell()
        if raw_cell is None:
            raw_cell = self.raw_cell
        try:
            content = shell_.transform_cell(raw_cell)
        except Exception:
            content = raw_cell
        ast_rewriter, syntax_augmenters = shell_.make_rewriter_and_syntax_augmenters(
            path=path
        )
        for aug in syntax_augmenters:
            content = aug(content)
        return ast_rewriter, content

    def raw_and_sanitized_content(self, path: Optional[str] = None) -> Tuple[str, str]:
        raw_cell = self.raw_cell
        return raw_cell, self._rewriter_and_sanitized_content(raw_cell, path=path)[1]

    def make_ipython_name(self) -> str:
        cache = shell().compile.cache
        kwargs = {}
        if "raw_code" in inspect.signature(cache).parameters:
            kwargs["raw_code"] = self.raw_cell
        return cache(self.transformed_cell, self.cell_ctr, **kwargs)

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
            or self.last_ast_content is None
            or len(self.last_ast_content) != len(self.current_content)
            or self.last_ast_content != self.current_content
        ):
            path = self.make_ipython_name()
            rewriter, content = self._rewriter_and_sanitized_content(path=path)
            self._cached_ast = ast.parse(content)
            self.last_ast_content = self.current_content
            if rewriter is not None:
                with self.override_current_cell():
                    rewriter.visit(self._cached_ast)
        return self._cached_ast

    @property
    def num_original_stmts(self) -> int:
        return len(self.to_ast().body)

    @property
    def num_stmts(self) -> int:
        return self.num_original_stmts + (self._extra_stmt is not None)

    @property
    def is_current_for_id(self) -> bool:
        return self._current_cell_by_cell_id.get(self.cell_id, None) is self

    @property
    def is_current(self) -> bool:
        return self.is_current_for_id

    @classmethod
    def current_cell(cls) -> "Cell":
        return cls._override_current_cell or cls._cell_by_cell_ctr[cls._cell_counter]

    @classmethod
    def current(cls) -> "Cell":
        return cls.current_cell()

    def get_max_used_live_symbol_cell_counter(
        self,
        live_symbols: Set[ResolvedSymbol],
        filter_to_reactive: bool = False,
        filter_to_cascading_reactive: bool = False,
        dead_symbols: Optional[Set["Symbol"]] = None,
    ) -> int:
        min_allowed_cell_position_by_symbol: Optional[Dict["Symbol", int]] = None
        flow_ = flow()
        if (
            flow_.mut_settings.exec_schedule
            == ExecutionSchedule.HYBRID_DAG_LIVENESS_BASED
            and flow_.mut_settings.flow_order == FlowDirection.IN_ORDER
        ):
            min_allowed_cell_position_by_symbol = {}
            for _ in flow_.mut_settings.iter_slicing_contexts():
                for pid, syms in self.directional_parents.items():
                    for sym in syms:
                        min_allowed_cell_position_by_symbol[sym] = max(
                            min_allowed_cell_position_by_symbol.get(sym, -1),
                            self.from_id(pid).position,
                        )
        with self._override_position_index_for_current_flow_semantics():
            max_used_cell_ctr = -1
            this_cell_pos = self.position
            for resolved in live_symbols:
                if resolved.is_blocking:
                    continue
                if filter_to_cascading_reactive and not resolved.is_cascading_reactive:
                    continue
                if (
                    filter_to_reactive
                    and not resolved.is_reactive
                    and not flow().is_updated_reactive(resolved.sym)
                ):
                    continue
                live_sym_updated_cell_ctr = resolved.timestamp.cell_num
                if (
                    live_sym_updated_cell_ctr
                    in self._used_cell_counters_by_live_symbol.get(resolved.sym, set())
                ):
                    used_cell_position = self.at_timestamp(
                        live_sym_updated_cell_ctr
                    ).position
                    if this_cell_pos >= used_cell_position:
                        if (
                            min_allowed_cell_position_by_symbol is None
                            or used_cell_position
                            >= min_allowed_cell_position_by_symbol.get(
                                resolved.sym, cast(int, float("inf"))
                            )
                        ):
                            max_used_cell_ctr = max(
                                max_used_cell_ctr,
                                live_sym_updated_cell_ctr,
                                resolved.sym._override_ready_liveness_cell_num,
                            )
            for sym in dead_symbols or []:
                if not sym.is_import:
                    continue
                try:
                    module_symbol = flow_.global_scope.lookup_symbol_by_qualified_name(
                        sym.imported_module
                    )
                except (ValueError, TypeError):
                    module_symbol = None
                if module_symbol is None:
                    continue
                max_used_cell_ctr = max(
                    max_used_cell_ctr,
                    module_symbol._override_ready_liveness_cell_num,
                )
            return max_used_cell_ctr

    def _get_live_dead_modified_symbol_refs(
        self, update_liveness_time_versions: bool
    ) -> Tuple[Set[LiveSymbolRef], Set[SymbolRef], Set[SymbolRef], bool]:
        live_symbol_refs: Set[LiveSymbolRef] = set()
        dead_symbol_refs: Set[SymbolRef] = set()
        modified_symbol_refs: Set[SymbolRef] = set()
        if self.override_live_refs is None and self.override_dead_refs is None:
            (
                live_symbol_refs,
                dead_symbol_refs,
                modified_symbol_refs,
            ) = compute_live_dead_symbol_refs(
                self.to_ast(),
                scope=flow().global_scope,
                include_killed_live=self.cell_ctr > 0,
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
        return (
            live_symbol_refs,
            dead_symbol_refs,
            modified_symbol_refs,
            update_liveness_time_versions,
        )

    def check_and_resolve_symbols(
        self,
        update_liveness_time_versions: bool = False,
    ) -> CheckerResult:
        (
            live_symbol_refs,
            dead_symbol_refs,
            modified_symbol_refs,
            update_liveness_time_versions,
        ) = self._get_live_dead_modified_symbol_refs(update_liveness_time_versions)
        with flow().override_child_cell(self):
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
        global_scope = flow().global_scope
        dead_symbols, _ = get_symbols_for_references(dead_symbol_refs, global_scope)
        modified_symbols, _ = get_symbols_for_references(
            modified_symbol_refs, global_scope
        )
        for sym in list(modified_symbols):
            modified_symbols |= set(
                subsym
                for subsym in sym.get_namespace_symbols(recurse=True)
                if not subsym._timestamp.is_initialized
            )
        if (
            self.last_check_content is None
            or self.last_check_content == self.current_content
        ):
            dead_symbols |= self.static_writes
            modified_symbols |= self.static_writes
        for resolved in live_resolved_symbols:
            if resolved.is_deep:
                resolved.sym.cells_where_deep_live.add(self)
            else:
                resolved.sym.cells_where_shallow_live.add(self)
            self.add_used_cell_counter(resolved.sym, resolved.timestamp.cell_num)
        used_cells = {resolved.timestamp.cell_num for resolved in live_resolved_symbols}
        return CheckerResult(
            live=live_resolved_symbols,
            unresolved_live_refs=unresolved_live_refs,
            used_cells=used_cells,
            live_cells=live_cells,
            dead=dead_symbols,
            modified=modified_symbols,
            typechecks=self._typechecks(live_cells, live_resolved_symbols),
        )

    def compute_phantom_cell_info(self, used_cells: Set[int]) -> Dict[IdType, Set[int]]:
        used_cell_counters_by_cell_id = defaultdict(set)
        used_cell_counters_by_cell_id[self.cell_id].add(self.exec_counter())
        for cell_num in used_cells:
            used_cell_counters_by_cell_id[self.at_timestamp(cell_num).cell_id].add(
                cell_num
            )
        return {
            cell_id: cell_execs
            for cell_id, cell_execs in used_cell_counters_by_cell_id.items()
            if len(cell_execs) >= 2
        }

    def _build_typecheck_slice(
        self, live_cell_ctrs: Set[int], live_symbols: Set[ResolvedSymbol]
    ) -> str:
        # TODO: typecheck statically-resolvable nested symbols too, not just top-level
        live_cell_counters = {self.cell_ctr}
        for live_cell_num in live_cell_ctrs:
            if self.at_timestamp(live_cell_num).is_current_for_id:
                live_cell_counters.add(live_cell_num)
        live_cells = [self.at_timestamp(ctr) for ctr in sorted(live_cell_counters)]
        top_level_symbols = {sym.sym.get_top_level() for sym in live_symbols}
        return "{type_declarations}\n\n{content}".format(
            type_declarations="\n".join(
                f"{sym.name}: {sym.get_type_annotation_string()}"
                for sym in top_level_symbols
                if sym
            ),
            content="\n".join(
                live_cell.sanitized_content() for live_cell in live_cells
            ),
        )

    def _typechecks(
        self, live_cell_ctrs: Set[int], live_symbols: Set[ResolvedSymbol]
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

    def _get_stmt_timestamps(self) -> Set[Timestamp]:
        return {Timestamp(self.cell_ctr, i) for i in range(self.num_stmts)}

    @classmethod
    def compute_multi_slice_stmts(
        cls, slice_cells: Iterable["Cell"]
    ) -> List["Statement"]:
        timestamps: Set[Timestamp] = set()
        for cell in slice_cells:
            timestamps |= cell._get_stmt_timestamps()
        return cast(
            "List[Statement]",
            statements().make_multi_slice(timestamps),
        )

    def compute_slice_stmts(self) -> List["Statement"]:
        return self.compute_multi_slice_stmts([self])

    def slice(
        self,
        stmts: bool = False,
        seed_only: bool = False,
        format_type: Optional[Type[FormatType]] = None,
        include_cell_headers: bool = True,
    ) -> Slice:
        if stmts:
            return self.format_multi_slice(
                self.compute_slice_stmts(),
                blacken=True,
                seed_only=seed_only,
                format_type=format_type,
                include_cell_headers=include_cell_headers,
            )
        else:
            return self.format_slice(
                blacken=False,
                seed_only=seed_only,
                format_type=format_type,
                include_cell_headers=include_cell_headers,
            )

    def code(
        self,
        stmts: bool = False,
        format_type: Optional[Type[FormatType]] = None,
        include_cell_headers: bool = True,
    ) -> Slice:
        return self.slice(
            stmts=stmts,
            seed_only=True,
            format_type=format_type,
            include_cell_headers=include_cell_headers,
        )

    def to_function(self, *args, **kwargs):
        return self.code().to_function(*args, **kwargs)

    def reproduce(
        self, show_input: bool = True, show_output: bool = True, lookback: int = 0
    ) -> Any:
        cell_to_repro = self
        for _ in range(lookback):
            assert cell_to_repro.prev_cell is not None
            cell_to_repro = cell_to_repro.prev_cell
        if show_input:
            print_ = print
            print_(cell_to_repro.executed_content or "")
            max_len = max(
                len(line)
                for line in (cell_to_repro.executed_content or "").splitlines()
            )
            print_("-" * max_len)
        if show_output and cell_to_repro.captured_output is not None:
            cell_to_repro.captured_output.show(render_out_expr=False)
        return shell().user_ns["Out"].get(cell_to_repro.cell_ctr)


if len(_CodeCellContainer) == 0:
    _CodeCellContainer.append(Cell)
else:
    _CodeCellContainer[0] = Cell
