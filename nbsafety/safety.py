# -*- coding: future_annotations -*-
import ast
import astunparse
import asyncio
import builtins
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
import inspect
import json
import logging
import re
import sys
import shlex
import subprocess
from typing import cast, TYPE_CHECKING, NamedTuple

from IPython import get_ipython
from IPython.core.magic import register_cell_magic, register_line_magic

from nbsafety.analysis.live_refs import (
    compute_live_dead_symbol_refs,
    get_symbols_for_references,
    get_live_symbols_and_cells_for_references,
)
from nbsafety.ipython_utils import (
    CellNotRunYetError,
    ast_transformer_context,
    cell_counter,
    run_cell,
    save_number_of_currently_executing_cell,
)
from nbsafety import line_magics
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.data_model.scope import Scope, NamespaceScope
from nbsafety.data_model.timestamp import Timestamp
from nbsafety.run_mode import SafetyRunMode
from nbsafety import singletons
from nbsafety.tracing.safety_ast_rewriter import SafetyAstRewriter
from nbsafety.tracing.trace_manager import TraceManager

if TYPE_CHECKING:
    from typing import Any, Dict, Iterable, List, Set, Optional, Tuple, TypeVar, Union
    from types import FrameType
    from nbsafety.types import CellId, SupportedIndexType

    TimestampOrCounter = TypeVar('TimestampOrCounter', Timestamp, int)

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

_SAFETY_LINE_MAGIC = 'safety'
_NB_MAGIC_PATTERN = re.compile(r'(^%|^!|^cd |\?$)')


class NotebookSafetySettings(NamedTuple):
    store_history: bool
    test_context: bool
    use_comm: bool
    backwards_cell_staleness_propagation: bool
    track_dependencies: bool
    mark_stale_symbol_usages_unsafe: bool
    mark_typecheck_failures_unsafe: bool
    mark_phantom_cell_usages_unsafe: bool
    mode: SafetyRunMode


@dataclass
class MutableNotebookSafetySettings:
    trace_messages_enabled: bool
    highlights_enabled: bool
    static_slicing_enabled: bool
    dynamic_slicing_enabled: bool


class CheckerResult(NamedTuple):
    live: Set[DataSymbol]
    live_cells: Set[int]
    live_cells_from_calls: Set[int]
    dead: Set[DataSymbol]
    stale: Set[DataSymbol]


class NotebookSafety(singletons.NotebookSafety):
    """Holds all the state necessary to detect stale dependencies in Jupyter notebooks."""

    def __init__(self, cell_magic_name=None, use_comm=False, settrace=None, **kwargs):
        super().__init__()
        self.settings: NotebookSafetySettings = NotebookSafetySettings(
            store_history=kwargs.pop('store_history', True),
            test_context=kwargs.pop('test_context', False),
            use_comm=use_comm,
            backwards_cell_staleness_propagation=True,
            track_dependencies=True,
            mark_stale_symbol_usages_unsafe=kwargs.pop('mark_stale_symbol_usages_unsafe', True),
            mark_typecheck_failures_unsafe=kwargs.pop('mark_typecheck_failures_unsafe', False),
            mark_phantom_cell_usages_unsafe=kwargs.pop('mark_phantom_cell_usages_unsafe', False),
            mode=SafetyRunMode.get(),
        )
        self.mut_settings: MutableNotebookSafetySettings = MutableNotebookSafetySettings(
            trace_messages_enabled=kwargs.pop('trace_messages_enabled', False),
            highlights_enabled=kwargs.pop('highlights_enabled', True),
            static_slicing_enabled=kwargs.pop('static_slicing_enabled', True),
            dynamic_slicing_enabled=kwargs.pop('dynamic_slicing_enabled', True),
        )
        # Note: explicitly adding the types helps PyCharm intellisense
        self.settrace = settrace or sys.settrace
        self.namespaces: Dict[int, NamespaceScope] = {}
        self.aliases: Dict[int, Set[DataSymbol]] = defaultdict(set)
        self.global_scope: Scope = Scope()
        self.updated_symbols: Set[DataSymbol] = set()
        self.statement_cache: Dict[int, Dict[int, ast.stmt]] = defaultdict(dict)
        self.ast_node_by_id: Dict[int, ast.AST] = {}
        self.loop_iter_flag_names: Set[str] = set()
        self.cell_id_by_ast_id: Dict[int, CellId] = {}
        self.parent_node_by_id: Dict[int, ast.AST] = {}
        # TODO: we have a lot of fields concerning cells; they should probably get their own
        #  abstraction in the data model via a dedicated class
        self.cell_content_by_counter: Dict[int, str] = {}
        self.cell_ast_by_counter: Dict[int, ast.Module] = {}
        self.statement_to_func_cell: Dict[int, DataSymbol] = {}
        self.cell_counter_by_live_symbol: Dict[DataSymbol, Set[int]] = defaultdict(set)
        self.live_symbols_by_cell_counter: Dict[int, Set[DataSymbol]] = defaultdict(set)
        self.cell_counters_needing_typecheck: Set[int] = set()
        self._typecheck_error_cells: Set[CellId] = set()
        self._counter_by_cell_id: Dict[CellId, int] = {}
        self._cell_id_by_counter: Dict[int, CellId] = {}
        self._active_cell_id: Optional[str] = None
        self._run_cells: Set[CellId] = set()
        self.active_cell_position_idx = -1
        self._last_execution_counter = 0
        self.safety_issue_detected = False
        if cell_magic_name is None:
            self._cell_magic = None
        else:
            self._cell_magic = self._make_cell_magic(cell_magic_name)
        self._line_magic = self._make_line_magic()
        self._last_refused_code: Optional[str] = None
        self._prev_cell_stale_symbols: Set[DataSymbol] = set()
        self._cell_counter = 1
        self._cell_name_to_cell_num_mapping: Dict[str, int] = {}
        self._exception_raised_during_execution: Optional[Exception] = None
        self._saved_debug_message: Optional[str] = None
        if use_comm:
            get_ipython().kernel.comm_manager.register_target(__package__, self._comm_target)

    @property
    def is_develop(self) -> bool:
        return self.settings.mode == SafetyRunMode.DEVELOP

    @property
    def is_test(self) -> bool:
        return self.settings.test_context

    @property
    def trace_messages_enabled(self) -> bool:
        return self.mut_settings.trace_messages_enabled

    @trace_messages_enabled.setter
    def trace_messages_enabled(self, new_val) -> None:
        self.mut_settings.trace_messages_enabled = new_val

    def get_first_full_symbol(self, obj_id: int) -> Optional[DataSymbol]:
        # TODO: also avoid anonymous namespaces?
        for alias in self.aliases.get(obj_id, []):
            if not alias.is_anonymous:
                return alias
        return None

    def cell_counter(self) -> int:
        if self.settings.store_history:
            return cell_counter()
        else:
            return self._cell_counter

    def reset_cell_counter(self):
        # only called in test context
        assert not self.settings.store_history
        for sym in self.all_data_symbols():
            sym._timestamp = sym._max_inner_timestamp = sym.required_timestamp = Timestamp.uninitialized()
            sym.timestamp_by_used_time.clear()
            sym.timestamp_by_liveness_time_by_cell_counter.clear()
        self._cell_counter = 1

    def set_exception_raised_during_execution(self, new_val: Optional[Exception] = None) -> Optional[Exception]:
        ret = self._exception_raised_during_execution
        self._exception_raised_during_execution = new_val
        return ret

    def get_position(self, frame: FrameType) -> Tuple[Optional[int], int]:
        try:
            cell_num = self._cell_name_to_cell_num_mapping.get(frame.f_code.co_filename, None)
            return cell_num, frame.f_lineno
        except KeyError as e:
            logger.error('key error while retrieving cell for %s', frame.f_code.co_filename)
            raise e

    def set_name_to_cell_num_mapping(self, frame: FrameType):
        self._cell_name_to_cell_num_mapping[frame.f_code.co_filename] = self.cell_counter()

    def is_cell_file(self, fname: str) -> bool:
        return fname in self._cell_name_to_cell_num_mapping

    def set_active_cell(self, cell_id, position_idx=-1):
        self._active_cell_id = cell_id
        if position_idx is not None:
            self.active_cell_position_idx = position_idx

    def _comm_target(self, comm, open_msg):
        @comm.on_msg
        def _responder(msg):
            request = msg['content']['data']
            self.handle(request, comm=comm)

        comm.send({'type': 'establish'})

    def handle(self, request, comm=None):
        if request['type'] == 'change_active_cell':
            self.set_active_cell(request['active_cell_id'], position_idx=request.get('active_cell_order_idx', -1))
        elif request['type'] == 'cell_freshness':
            if self._active_cell_id is None:
                self._active_cell_id = request.get('executed_cell_id', None)
                if self._active_cell_id is not None:
                    self._counter_by_cell_id[self._active_cell_id] = self._last_execution_counter
                    self._cell_id_by_counter[self._last_execution_counter] = self._active_cell_id
                    self._run_cells.add(self._active_cell_id)
                    for ast_id in [ast_id for ast_id, cell_id in self.cell_id_by_ast_id.items() if cell_id is None]:
                        self.cell_id_by_ast_id[ast_id] = self._active_cell_id
            cell_id = request.get('executed_cell_id', None)
            cells_by_id = request['content_by_cell_id']
            if self.settings.backwards_cell_staleness_propagation:
                order_index_by_id = None
                last_cell_exec_position_idx = -1
            else:
                order_index_by_id = request['order_index_by_cell_id']
                last_cell_exec_position_idx = order_index_by_id.get(cell_id, -1)
            response = self.check_and_link_multiple_cells(cells_by_id, order_index_by_id)
            response['type'] = 'cell_freshness'
            response['last_cell_exec_position_idx'] = last_cell_exec_position_idx
            if comm is not None:
                comm.send(response)
        else:
            logger.error('Unsupported request type for request %s' % request)

    def check_and_link_multiple_cells(
        self,
        content_by_cell_id: Dict[CellId, str],
        order_index_by_cell_id: Optional[Dict[CellId, int]] = None
    ) -> Dict[str, Any]:
        if not self.mut_settings.highlights_enabled:
            return {
                'stale_cells': [],
                'fresh_cells': [],
                'stale_links': {},
                'refresher_links': {},
            }
        stale_cells = set()
        fresh_cells = []
        stale_symbols_by_cell_id: Dict[CellId, Set[DataSymbol]] = {}
        killing_cell_ids_for_symbol: Dict[DataSymbol, Set[CellId]] = defaultdict(set)
        cell_ids_needing_typecheck = self.get_cell_ids_needing_typecheck()
        for cell_id, cell_content in content_by_cell_id.items():
            if cell_id not in self._run_cells:
                continue
            if (
                order_index_by_cell_id is not None
                and order_index_by_cell_id.get(cell_id, -1) <= self.active_cell_position_idx
            ):
                continue
            try:
                checker_result = self._check_cell_and_resolve_symbols(cell_content, self._counter_by_cell_id[cell_id])
                stale_symbols, dead_symbols = checker_result.stale, checker_result.dead
                if len(stale_symbols) > 0:
                    stale_symbols_by_cell_id[cell_id] = stale_symbols
                    stale_cells.add(cell_id)

                for dead_sym in dead_symbols:
                    killing_cell_ids_for_symbol[dead_sym].add(cell_id)

                if self.settings.mark_typecheck_failures_unsafe and cell_id in cell_ids_needing_typecheck:
                    typecheck_slice = self._build_typecheck_slice(cell_id, content_by_cell_id, checker_result)
                    try:
                        # TODO: parse the output in order to pass up to the user
                        ret = subprocess.call(f"mypy -c {shlex.quote(typecheck_slice)}", shell=True)
                        if ret == 0:
                            self._typecheck_error_cells.discard(cell_id)
                        else:
                            self._typecheck_error_cells.add(cell_id)
                    except Exception as e:
                        logger.info('Exception ocurred during type checking: %s', e)
                        self._typecheck_error_cells.discard(cell_id)

                if self.settings.mark_phantom_cell_usages_unsafe:
                    # TODO: implement phantom cell usage detection here
                    pass

                if (
                    self._get_max_timestamp_cell_num_for_symbols(checker_result.live) >
                    self._counter_by_cell_id.get(cell_id, cast(int, float('inf')))
                ) and cell_id not in stale_cells and cell_id not in self._typecheck_error_cells:
                    fresh_cells.append(cell_id)
            except SyntaxError:
                continue
        stale_links: Dict[CellId, Set[CellId]] = defaultdict(set)
        refresher_links: Dict[CellId, List[CellId]] = defaultdict(list)
        for stale_cell_id in stale_cells:
            stale_syms = stale_symbols_by_cell_id[stale_cell_id]
            refresher_cell_ids: Set[CellId] = set()
            refresher_cell_ids = refresher_cell_ids.union(
                *(killing_cell_ids_for_symbol[stale_sym] for stale_sym in stale_syms)
            )
            stale_links[stale_cell_id] = refresher_cell_ids
        stale_link_changes = True
        # transitive closer up until we hit non-stale refresher cells
        while stale_link_changes:
            stale_link_changes = False
            for stale_cell_id in stale_cells:
                new_stale_links = set(stale_links[stale_cell_id])
                original_length = len(new_stale_links)
                for refresher_cell_id in stale_links[stale_cell_id]:
                    if refresher_cell_id not in stale_cells:
                        continue
                    new_stale_links |= stale_links[refresher_cell_id]
                new_stale_links.discard(stale_cell_id)
                stale_link_changes = stale_link_changes or original_length != len(new_stale_links)
                stale_links[stale_cell_id] = new_stale_links
        for stale_cell_id in stale_cells:
            stale_links[stale_cell_id] -= stale_cells
            for refresher_cell_id in stale_links[stale_cell_id]:
                refresher_links[refresher_cell_id].append(stale_cell_id)
        if self.settings.mark_typecheck_failures_unsafe:
            # TODO: actually do the type checking
            #  nested symbols in particular will need some thought
            self.cell_counters_needing_typecheck.clear()
        for typecheck_error_cell in self._typecheck_error_cells:
            if typecheck_error_cell not in stale_links:
                stale_links[typecheck_error_cell] = set()
        return {
            # TODO: we should probably have separate fields for stale vs non-typechecking cells,
            #  or at least change the name to a more general "unsafe_cells" or equivalent
            'stale_cells': list(stale_cells | self._typecheck_error_cells),
            'fresh_cells': fresh_cells,
            'stale_links': {
                stale_cell_id: list(refresher_cell_ids)
                for stale_cell_id, refresher_cell_ids in stale_links.items()
            },
            'refresher_links': refresher_links,
        }

    @staticmethod
    def _get_cell_ast(cell):
        lines = []
        for line in cell.strip().split('\n'):
            # TODO: figure out more robust strategy for filtering / transforming lines for the ast parser
            # we filter line magics, but for %time, we would ideally like to trace the statement being timed
            # TODO: how to do this?
            if _NB_MAGIC_PATTERN.search(line) is None:
                lines.append(line)
        return ast.parse('\n'.join(lines))

    def _get_max_timestamp_cell_num_for_symbols(self, symbols: Set[DataSymbol]) -> int:
        max_timestamp_cell_num = -1
        for dsym in symbols:
            max_timestamp_cell_num = max(max_timestamp_cell_num, dsym.timestamp.cell_num)
        return max_timestamp_cell_num

    def _build_typecheck_slice(
        self, cell_id: CellId, content_by_cell_id: Dict[CellId, str], checker_result: CheckerResult
    ) -> str:
        live_cell_counters = {self._counter_by_cell_id[cell_id]}
        for live_cell_num in checker_result.live_cells_from_calls:
            live_cell_id = self._cell_id_by_counter[live_cell_num]
            if self._counter_by_cell_id[live_cell_id] == live_cell_num:
                live_cell_counters.add(live_cell_num)
        live_cell_ids = [self._cell_id_by_counter[ctr] for ctr in sorted(live_cell_counters)]
        top_level_symbols = {sym.get_top_level() for sym in checker_result.live}
        top_level_symbols.discard(None)
        return '{type_declarations}\n\n{content}'.format(
            type_declarations='\n'.join(f'{sym.name}: {sym.get_type_annotation_string()}' for sym in top_level_symbols),
            content='\n'.join(content_by_cell_id[cell_id] for cell_id in live_cell_ids),
        )

    def _check_cell_and_resolve_symbols(
        self, cell: Union[ast.Module, str], cell_ctr: int, update_liveness_time_versions: bool = False
    ) -> CheckerResult:
        for dsym in self.live_symbols_by_cell_counter[cell_ctr]:
            dsym.timestamp_by_liveness_time_by_cell_counter[cell_ctr].clear()
        if isinstance(cell, str):
            cell = self._get_cell_ast(cell)
        live_symbol_refs, dead_symbol_refs = compute_live_dead_symbol_refs(cell, scope=self.global_scope)
        if update_liveness_time_versions:
            get_live_symbols_and_cells_for_references(
                live_symbol_refs, self.global_scope, cell_ctr, update_liveness_time_versions=True
            )
        live_symbols, live_cells_from_calls = get_live_symbols_and_cells_for_references(
            live_symbol_refs, self.global_scope, cell_ctr, update_liveness_time_versions=False
        )
        # only mark dead attrsubs as killed if we can traverse the entire chain
        dead_symbols, _ = get_symbols_for_references(
            dead_symbol_refs, self.global_scope, only_yield_successful_resolutions=True
        )
        stale_symbols = set(dsym for dsym in live_symbols if dsym.is_stale)
        self.live_symbols_by_cell_counter[cell_ctr] = live_symbols
        return CheckerResult(
            live=live_symbols,
            live_cells={sym.timestamp.cell_num for sym in live_symbols},
            live_cells_from_calls=live_cells_from_calls,
            dead=dead_symbols,
            stale=stale_symbols,
        )

    _MAX_STALE_SYM_WARNINGS = 10
    _MAX_STALE_CELL_WARNINGS = 10

    @staticmethod
    def _stale_sym_warning(sym: DataSymbol):
        if not sym.is_stale:
            raise ValueError('Expected node with stale ancestor; got %s' % sym)
        if not sym.timestamp.is_initialized:
            return
        fresher_symbols = sym.fresher_ancestors
        if len(fresher_symbols) == 0:
            fresher_symbols = fresher_symbols.union(
                *(ns_stale.fresher_ancestors for ns_stale in sym.namespace_stale_symbols)
            )
        logger.warning(
            f'`{sym.readable_name}` defined in cell {sym.defined_cell_num} '
            f'(last updated in cell {sym.timestamp.cell_num}) may depend on '
            f'old version(s) of [{", ".join(f"`{str(dep)}`" for dep in fresher_symbols if not dep.is_anonymous)}] '
            f'(latest update in cell {max(dep.timestamp.cell_num for dep in fresher_symbols if not dep.is_anonymous)}).'
        )

    @staticmethod
    def _stale_cell_warning(cell_execs: Set[int]):
        if len(cell_execs) < 2:
            raise ValueError('Expected cell that was executed at least twice')
        logger.warning(
            f'Detected usages of symbols across same cell executed multiple times (timestamps {cell_execs}) '
        )

    def _safety_precheck_cell(self, cell: str, cell_id: Optional[CellId]) -> bool:
        """
        This method statically checks the cell to be executed for stale symbols and other safety issues.
        If any safety issues are detected, it returns `True`. Otherwise (or on syntax error), return `False`.
        Furthermore, if this is the second time the user has attempted to execute the exact same code, we
        assume they want to override this checker. In the case of stale symbols, we temporarily mark any
        stale symbols as being not stale and return `False`.
        """
        # TODO: there is a lot of potential for code sharing between this and `check_and_link_multiple_cells`;
        #  we should improve the abstractions and take advantage of this. Right now we need to add bespoke code
        #  for various kinds of unsafe interaction detection in both places.
        try:
            cell_ast = self._get_cell_ast(cell)
        except SyntaxError:
            return False
        checker_result = self._check_cell_and_resolve_symbols(
            cell_ast, self.cell_counter(), update_liveness_time_versions=self.mut_settings.static_slicing_enabled
        )
        stale_symbols, live_symbols, live_cells = checker_result.stale, checker_result.live, checker_result.live_cells
        stale_sym_usage_warning_counter = 0
        phantom_cell_usage_warning_counter = 0
        if self._last_refused_code is None or cell != self._last_refused_code:
            if self.settings.mark_stale_symbol_usages_unsafe:
                self._prev_cell_stale_symbols = stale_symbols
                for sym in self._prev_cell_stale_symbols:
                    if stale_sym_usage_warning_counter >= self._MAX_STALE_SYM_WARNINGS:
                        logger.warning(f'{len(self._prev_cell_stale_symbols) - stale_sym_usage_warning_counter}'
                                       ' more symbols with stale dependencies skipped...')
                        break
                    self._stale_sym_warning(sym)
                    stale_sym_usage_warning_counter += 1
                if stale_sym_usage_warning_counter > 0:
                    logger.warning('\n\n(Run cell again to override and execute anyway.)')
            if self.settings.mark_phantom_cell_usages_unsafe:
                used_cell_counters_by_cell_id = defaultdict(set)
                used_cell_counters_by_cell_id[cell_id].add(self.cell_counter())
                for cell_num in live_cells:
                    used_cell_counters_by_cell_id[self._cell_id_by_counter[cell_num]].add(cell_num)
                phantom_cell_info = {
                    cell_id: cell_execs
                    for cell_id, cell_execs in used_cell_counters_by_cell_id.items()
                    if len(cell_execs) >= 2
                }
                for used_cell_execs in phantom_cell_info.values():
                    if phantom_cell_usage_warning_counter >= self._MAX_STALE_CELL_WARNINGS:
                        logger.warning(f'{len(phantom_cell_info) - phantom_cell_usage_warning_counter} '
                                       'more cells with symbol usages from phantom cells skipped...')
                        break
                    self._stale_cell_warning(used_cell_execs)
                    phantom_cell_usage_warning_counter += 1
            if self.settings.mark_typecheck_failures_unsafe:
                # TODO: implement typechecking here, same as in `check_and_link_multiple_cells`
                pass
        else:
            # Instead of breaking the dependency chain, simply refresh the nodes
            # with stale deps to their required cell numbers
            # TODO: temporary allow executions depending on phantom cells?
            for sym in self._prev_cell_stale_symbols:
                sym.temporary_disable_warnings()
            self._prev_cell_stale_symbols.clear()

        if stale_sym_usage_warning_counter + phantom_cell_usage_warning_counter > 0:
            self.safety_issue_detected = True
            self._last_refused_code = cell
            return True

        # For each of the live symbols, record their timestamp
        # at the time of liveness, for use with the dynamic slicer.
        for sym in live_symbols:
            self.cell_counter_by_live_symbol[sym].add(self.cell_counter())
            # sym.timestamp_by_liveness_time[self.cell_counter()] = sym.timestamp

        self._last_refused_code = None
        return False

    def get_cell_ids_needing_typecheck(self) -> Set[CellId]:
        cell_ids_needing_typecheck = set()
        for cell_ctr in self.cell_counters_needing_typecheck:
            cell_id = self._cell_id_by_counter[cell_ctr]
            if self._counter_by_cell_id[cell_id] == cell_ctr:
                cell_ids_needing_typecheck.add(cell_id)
        return cell_ids_needing_typecheck

    def _resync_symbols(self, symbols: Iterable[DataSymbol]):
        for dsym in symbols:
            if not dsym.containing_scope.is_global:
                continue
            obj = get_ipython().user_global_ns.get(dsym.name, None)
            if obj is None:
                continue
            if dsym.obj_id == id(obj):
                continue
            for alias in self.aliases[dsym.cached_obj_id] | self.aliases[dsym.obj_id]:
                containing_namespace = alias.containing_namespace
                if containing_namespace is None:
                    continue
                containing_obj = containing_namespace.obj
                if containing_obj is None:
                    continue
                # TODO: handle dict case too
                if isinstance(containing_obj, list) and containing_obj[-1] is obj:
                    containing_namespace._subscript_data_symbol_by_name.pop(alias.name, None)
                    alias.name = len(containing_obj) - 1
                    alias.update_obj_ref(obj)
                    containing_namespace._subscript_data_symbol_by_name[alias.name] = alias
            self.aliases[dsym.cached_obj_id].discard(dsym)
            self.aliases[dsym.obj_id].discard(dsym)
            self.aliases[id(obj)].add(dsym)
            dsym.update_obj_ref(obj)

    def create_dag_metadata(self) -> Dict[int, Dict[str, Union[List[int], List[str], Dict[str, Dict[str, str]]]]]:
        cell_num_to_used_imports: Dict[int, Set[DataSymbol]] = defaultdict(set)
        cell_num_to_dynamic_inputs: Dict[int, Set[DataSymbol]] = defaultdict(set)
        cell_num_to_dynamic_outputs: Dict[int, Set[DataSymbol]] = defaultdict(set)
        cell_num_to_dynamic_cell_parents: Dict[int, Set[int]] = defaultdict(set)
        cell_num_to_dynamic_cell_children: Dict[int, Set[int]] = defaultdict(set)

        for sym in self.all_data_symbols():
            top_level_sym = sym.get_top_level()
            if top_level_sym is None or not top_level_sym.is_globally_accessible or top_level_sym.is_anonymous:
                # TODO: also skip lambdas
                continue
            for used_time, sym_timestamp_when_used in sym.timestamp_by_used_time.items():
                if top_level_sym.is_import:
                    cell_num_to_used_imports[used_time.cell_num].add(top_level_sym)
                else:
                    if sym_timestamp_when_used < used_time:
                        cell_num_to_dynamic_cell_parents[used_time.cell_num].add(sym_timestamp_when_used.cell_num)
                        cell_num_to_dynamic_inputs[used_time.cell_num].add(top_level_sym)
                        cell_num_to_dynamic_cell_children[sym_timestamp_when_used.cell_num].add(used_time.cell_num)
                    cell_num_to_dynamic_outputs[sym_timestamp_when_used.cell_num].add(top_level_sym)
            if not top_level_sym.is_import:
                for updated_time in sym.updated_timestamps:
                    # TODO: distinguished between used / unused outputs?
                    cell_num_to_dynamic_outputs[updated_time.cell_num].add(top_level_sym)

        cell_metadata: Dict[int, Dict[str, Union[List[int], List[str], Dict[str, Dict[str, str]]]]] = {}
        all_relevant_cells = (
            cell_num_to_used_imports.keys() |
            cell_num_to_dynamic_inputs.keys() |
            cell_num_to_dynamic_outputs.keys() |
            cell_num_to_dynamic_cell_parents.keys() |
            cell_num_to_dynamic_cell_children.keys()
        )
        for cell_num in all_relevant_cells:
            cell_imports = [dsym.get_import_string() for dsym in cell_num_to_used_imports[cell_num]]
            input_symbols = {
                str(dsym): {
                    'type': dsym.get_type_annotation_string()
                } for dsym in cell_num_to_dynamic_inputs[cell_num]
            }
            output_symbols = {
                str(dsym): {
                    'type': dsym.get_type_annotation_string()
                } for dsym in cell_num_to_dynamic_outputs[cell_num]
            }
            parent_cells = list(cell_num_to_dynamic_cell_parents[cell_num])
            child_cells = list(cell_num_to_dynamic_cell_children[cell_num])
            cell_metadata[cell_num] = {
                'cell_imports': cell_imports,
                'input_symbols': input_symbols,
                'output_symbols': output_symbols,
                'parent_cells': parent_cells,
                'child_cells': child_cells,
            }
        return cell_metadata

    def compute_slice(self, cell_num: int, stmt_level: bool = False) -> Dict[int, str]:
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
        if cell_num not in self.cell_content_by_counter.keys():
            raise CellNotRunYetError(f'Cell {cell_num} has not been run yet.')

        if stmt_level:
            stmts_by_cell_num = self.compute_slice_stmts(cell_num)
            stmts_by_cell_num.pop(cell_num, None)
            ret = {
                ctr: '\n'.join(astunparse.unparse(stmt).strip() for stmt in stmts)
                for ctr, stmts in stmts_by_cell_num.items()
            }
            ret[cell_num] = self.cell_content_by_counter[cell_num]
            return ret
        else:
            deps: Set[int] = self._compute_slice_impl(cell_num, set(), defaultdict(set), defaultdict(set))
            return {dep: self.cell_content_by_counter[dep] for dep in deps}

    def compute_slice_stmts(self, cell_num: int) -> Dict[int, List[ast.stmt]]:
        if cell_num not in self.cell_content_by_counter.keys():
            raise CellNotRunYetError(f'Cell {cell_num} has not been run yet.')

        deps_stmt: Set[Timestamp] = self._compute_slice_impl(
            Timestamp(cell_num, -1), set(), defaultdict(set), defaultdict(set)
        )
        stmts_by_cell_num = defaultdict(list)
        seen_stmt_ids = set()
        for ts in sorted(deps_stmt):
            if ts.cell_num > cell_num:
                break
            stmt = self.cell_ast_by_counter[ts.cell_num].body[ts.stmt_num]
            stmt_id = id(stmt)
            if stmt is None or stmt_id in seen_stmt_ids:
                continue
            seen_stmt_ids.add(stmt_id)
            if stmt is not None:
                stmts_by_cell_num[ts.cell_num].append(stmt)
        stmts_by_cell_num[cell_num] = list(ast.parse(self.cell_content_by_counter[cell_num]).body)
        return dict(stmts_by_cell_num)

    def _compute_slice_impl(
        self,
        seed_ts: TimestampOrCounter,
        dependencies: Set[TimestampOrCounter],
        timestamp_to_dynamic_ts_deps: Dict[TimestampOrCounter, Set[TimestampOrCounter]],
        timestamp_to_static_ts_deps: Dict[TimestampOrCounter, Set[TimestampOrCounter]],
    ) -> Set[TimestampOrCounter]:

        for sym in self.all_data_symbols():
            if self.mut_settings.dynamic_slicing_enabled:
                for used_time, sym_timestamp_when_used in sym.timestamp_by_used_time.items():
                    if sym_timestamp_when_used < used_time:
                        if isinstance(seed_ts, Timestamp):
                            timestamp_to_dynamic_ts_deps[used_time].add(sym_timestamp_when_used)
                        else:
                            timestamp_to_dynamic_ts_deps[used_time.cell_num].add(sym_timestamp_when_used.cell_num)
            if self.mut_settings.static_slicing_enabled:
                for timestamp_by_liveness_time in sym.timestamp_by_liveness_time_by_cell_counter.values():
                    for liveness_time, sym_timestamp_when_used in list(timestamp_by_liveness_time.items()):
                        if sym_timestamp_when_used < liveness_time:
                            if isinstance(seed_ts, Timestamp):
                                timestamp_to_static_ts_deps[liveness_time].add(sym_timestamp_when_used)
                            else:
                                timestamp_to_static_ts_deps[liveness_time.cell_num].add(
                                    sym_timestamp_when_used.cell_num
                                )

        # ensure we at least get the static deps
        self._get_ts_dependencies(
            seed_ts, dependencies, timestamp_to_dynamic_ts_deps, timestamp_to_static_ts_deps
        )
        if isinstance(seed_ts, Timestamp):
            for ts in list(timestamp_to_dynamic_ts_deps.keys() | timestamp_to_static_ts_deps.keys()):
                if ts.cell_num == seed_ts.cell_num:
                    self._get_ts_dependencies(
                        ts, dependencies, timestamp_to_dynamic_ts_deps, timestamp_to_static_ts_deps
                    )
        return dependencies

    def _get_ts_dependencies(
        self,
        timestamp: TimestampOrCounter,
        dependencies: Set[TimestampOrCounter],
        timestamp_to_dynamic_ts_deps: Dict[TimestampOrCounter, Set[TimestampOrCounter]],
        timestamp_to_static_ts_deps: Dict[TimestampOrCounter, Set[TimestampOrCounter]],
    ) -> None:
        """
        For a given cell, this function recursively populates a set of
        cell numbers that the given cell depends on, based on the live symbols.

        Args:
            - dependencies (set<int>): set of cell numbers so far that exist
            - cell_num (int): current cell to get dependencies for
            - cell_num_to_dynamic_deps (dict<int, set<int>>): mapping from cell 
            num to version of cells where its symbols were used
            - cell_num_to_static_deps (dict<int, set<int>>): mapping from cell 
            num to version of cells where its symbols were defined

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
        logger.info('static ts deps for %d: %s', timestamp, static_ts_deps)

        # For each dependent cell, recursively get their dependencies
        for ts in dep_timestamps - dependencies:
            self._get_ts_dependencies(
                ts, dependencies, timestamp_to_dynamic_ts_deps, timestamp_to_static_ts_deps
            )

    async def safe_execute(self, cell: str, is_async: bool, run_cell_func):
        if self._saved_debug_message is not None:  # pragma: no cover
            logger.error(self._saved_debug_message)
            self._saved_debug_message = None
        ret = None
        with save_number_of_currently_executing_cell():
            self._last_execution_counter = self.cell_counter()

            cell_id, self._active_cell_id = self._active_cell_id, None
            if cell_id is not None:
                self._counter_by_cell_id[cell_id] = self._last_execution_counter
                self._cell_id_by_counter[self._last_execution_counter] = cell_id

            if cell_id is not None:
                # add to run_cells even if it fails precheck, since an attempt
                # to run indicates that the user is interested in this cell, so
                # we should add this cell to the set of cells that get checked
                # in various ways in the post-exec hook.
                self._run_cells.add(cell_id)

            # Stage 1: Precheck.
            if self._safety_precheck_cell(cell, cell_id) and self.settings.mark_stale_symbol_usages_unsafe:
                # set this back in case we need it (e.g. user overrides and reruns)
                self._active_cell_id = cell_id
                # FIXME: hack to increase cell number
                #  ideally we shouldn't show a cell number at all if we fail precheck since nothing executed
                if is_async:
                    ret = await run_cell_func('None')  # pragma: no cover
                else:
                    ret = run_cell_func('None')
                return ret

            # Stage 2: Trace / run the cell, updating dependencies as they are encountered.
            try:
                self.cell_content_by_counter[self._last_execution_counter] = cell
                with self._tracing_context(cell_id):
                    if is_async:
                        ret = await run_cell_func(cell)  # pragma: no cover
                    else:
                        ret = run_cell_func(cell)
                # Stage 2.1: resync any defined symbols that could have gotten out-of-sync
                #  due to tracing being disabled

                self._resync_symbols([
                    # TODO: avoid bad performance by only iterating over symbols updated in this cell
                    sym for sym in self.all_data_symbols() if sym.timestamp.cell_num == self.cell_counter()
                ])
                self._gc()
            except Exception as e:
                # logger.exception('exception occurred during execution')
                if self.is_test:
                    self.set_exception_raised_during_execution(e)
            finally:
                if not self.settings.store_history:
                    self._cell_counter += 1
                return ret

    def _make_cell_magic(self, cell_magic_name):
        # this is to avoid capturing `self` and creating an extra reference to the singleton
        store_history = self.settings.store_history

        def _run_cell_func(cell):
            run_cell(cell, store_history=store_history)

        def _dependency_safety(_, cell: str):
            asyncio.get_event_loop().run_until_complete(
                asyncio.wait([singletons.nbs().safe_execute(cell, False, _run_cell_func)])
            )

        # FIXME (smacke): probably not a great idea to rely on this
        _dependency_safety.__name__ = cell_magic_name
        return register_cell_magic(_dependency_safety)

    @contextmanager
    def _tracing_context(self, cell_id: CellId):
        self.updated_symbols.clear()

        try:
            with TraceManager.instance().tracing_context():
                with ast_transformer_context([SafetyAstRewriter(cell_id)]):
                    yield
        finally:
            TraceManager.clear_instance()

    def _make_line_magic(self):
        line_magic_names = [f[0] for f in inspect.getmembers(line_magics) if inspect.isfunction(f[1])]

        def _handle(cmd, line):
            if cmd in ('deps', 'show_deps', 'show_dependency', 'show_dependencies'):
                return line_magics.show_deps(line)
            elif cmd in ('stale', 'show_stale'):
                return line_magics.show_stale(line)
            elif cmd == 'trace_messages':
                return line_magics.trace_messages(line)
            elif cmd in ('hls', 'nohls', 'highlight', 'highlights'):
                return line_magics.set_highlights(cmd, line)
            elif cmd in ('dag', 'make_dag', 'cell_dag', 'make_cell_dag'):
                return json.dumps(self.create_dag_metadata(), indent=2)
            elif cmd in ('slice', 'make_slice', 'gather_slice'):
                return line_magics.make_slice(line)
            elif cmd == 'remove_dependency':
                return line_magics.remove_dep(line)
            elif cmd in ('add_dependency', 'add_dep'):
                return line_magics.add_dep(line)
            elif cmd == 'turn_off_warnings_for':
                return line_magics.turn_off_warnings_for(line)
            elif cmd == 'turn_on_warnings_for':
                return line_magics.turn_on_warnings_for(line)
            elif cmd in line_magic_names:
                logger.warning('We have a magic for %s, but have not yet registered it', cmd)
                return None
            else:
                logger.warning(line_magics.USAGE)
                return None

        def _safety(line: str):
            # this is to avoid capturing `self` and creating an extra reference to the singleton
            try:
                cmd, line = line.split(' ', 1)
            except ValueError:
                cmd, line = line, ''
            try:
                line, fname = line.split('>', 1)
            except ValueError:
                line, fname = line, None
            line = line.strip()
            if fname is not None:
                fname = fname.strip()

            outstr = _handle(cmd, line)
            if outstr is None:
                return

            if fname is None:
                print(outstr)
            else:
                with open(fname, 'w') as f:
                    f.write(outstr)

        # FIXME (smacke): probably not a great idea to rely on this
        _safety.__name__ = _SAFETY_LINE_MAGIC
        return register_line_magic(_safety)

    @property
    def dependency_tracking_enabled(self):
        return self.settings.track_dependencies

    @property
    def cell_magic_name(self):
        return self._cell_magic.__name__

    @property
    def line_magic_name(self):
        return self._line_magic.__name__

    def all_data_symbols(self) -> Iterable[DataSymbol]:
        for alias_set in self.aliases.values():
            yield from alias_set

    def test_and_clear_detected_flag(self):
        ret = self.safety_issue_detected
        self.safety_issue_detected = False
        return ret

    def _gc(self):
        # Need to do the garbage check and the collection separately
        garbage_syms = [dsym for dsym in self.all_data_symbols() if dsym.is_garbage]
        for dsym in garbage_syms:
            logger.info('collect sym %s', dsym)
            dsym.collect_self_garbage()

    def retrieve_namespace_attr_or_sub(self, obj: Any, attr_or_sub: SupportedIndexType, is_subscript: bool):
        try:
            if is_subscript:
                # TODO: more complete list of things that are checkable
                #  or could cause side effects upon subscripting
                return obj[attr_or_sub]
            else:
                if self.is_develop:
                    assert isinstance(attr_or_sub, str)
                return getattr(obj, cast(str, attr_or_sub))
        except (AttributeError, IndexError, KeyError):
            raise
        except Exception as e:
            if self.is_develop:
                logger.warning('unexpected exception: %s', e)
                logger.warning('object: %s', obj)
                logger.warning('attr / subscript: %s', attr_or_sub)
            raise e

    @staticmethod
    def make_loop_iter_flag_name(loop_node: Union[int, ast.AST]):
        loop_node_id = loop_node if isinstance(loop_node, int) else id(loop_node)
        return '_X5ix_NBSAFETY_LOOPED_ONCE_{}'.format(loop_node_id)
