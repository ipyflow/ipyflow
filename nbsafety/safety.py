# -*- coding: future_annotations -*-
import ast
import asyncio
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
import inspect
import json
import logging
import re
import sys
from typing import cast, TYPE_CHECKING, NamedTuple

from IPython import get_ipython
from IPython.core.magic import register_cell_magic, register_line_magic

from nbsafety.ipython_utils import (
    ast_transformer_context,
    run_cell,
    save_number_of_currently_executing_cell,
)
from nbsafety import line_magics
from nbsafety.data_model.code_cell import cells, ExecutedCodeCell
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.data_model.namespace import Namespace
from nbsafety.data_model.scope import Scope
from nbsafety.data_model.timestamp import Timestamp
from nbsafety.run_mode import ExecutionMode, SafetyRunMode
from nbsafety import singletons
from nbsafety.tracing.safety_ast_rewriter import SafetyAstRewriter
from nbsafety.tracing.trace_manager import TraceManager

if TYPE_CHECKING:
    from typing import Any, Dict, Iterable, List, Set, Optional, Tuple, Union
    from types import FrameType
    from nbsafety.types import CellId, SupportedIndexType

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
    exec_mode: ExecutionMode


class FrontendCheckerResult(NamedTuple):
    stale_cells: Set[CellId]
    fresh_cells: Set[CellId]
    new_fresh_cells: Set[CellId]
    stale_links: Dict[CellId, Set[CellId]]
    refresher_links: Dict[CellId, Set[CellId]]
    phantom_cell_info: Dict[CellId, Dict[CellId, Set[int]]]

    def to_json(self) -> Dict[str, Any]:
        return {
            'stale_cells': list(self.stale_cells),
            'fresh_cells': list(self.fresh_cells),
            'new_fresh_cells': list(self.new_fresh_cells),
            'stale_links': {cell_id: list(linked_cell_ids) for cell_id, linked_cell_ids in self.stale_links.items()},
            'refresher_links': {
                cell_id: list(linked_cell_ids) for cell_id, linked_cell_ids in self.refresher_links.items()
            },
        }


class NotebookSafety(singletons.NotebookSafety):
    """Holds all the state necessary to detect stale dependencies in Jupyter notebooks."""

    def __init__(self, cell_magic_name=None, use_comm=False, settrace=None, **kwargs):
        super().__init__()
        cells().clear()
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
            exec_mode=ExecutionMode(kwargs.pop('exec_mode', ExecutionMode.NORMAL)),
        )
        # Note: explicitly adding the types helps PyCharm intellisense
        self.settrace = settrace or sys.settrace
        self.namespaces: Dict[int, Namespace] = {}
        self.aliases: Dict[int, Set[DataSymbol]] = defaultdict(set)
        self.global_scope: Scope = Scope()
        self.updated_symbols: Set[DataSymbol] = set()
        self.statement_cache: Dict[int, Dict[int, ast.stmt]] = defaultdict(dict)
        self.ast_node_by_id: Dict[int, ast.AST] = {}
        self.loop_iter_flag_names: Set[str] = set()
        self.parent_node_by_id: Dict[int, ast.AST] = {}
        self.statement_to_func_cell: Dict[int, DataSymbol] = {}
        self._active_cell_id: Optional[str] = None
        self.active_cell_position_idx = -1
        self.safety_issue_detected = False
        if cell_magic_name is None:
            self._cell_magic = None
        else:
            self._cell_magic = self._make_cell_magic(cell_magic_name)
        self._line_magic = self._make_line_magic()
        self._prev_cell_stale_symbols: Set[DataSymbol] = set()
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
        return cells().exec_counter()

    def reset_cell_counter(self):
        # only called in test context
        assert not self.settings.store_history
        for sym in self.all_data_symbols():
            sym._timestamp = sym._max_inner_timestamp = sym.required_timestamp = Timestamp.uninitialized()
            sym.timestamp_by_used_time.clear()
            sym.timestamp_by_liveness_time.clear()
        cells().clear()

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
        self._cell_name_to_cell_num_mapping[frame.f_code.co_filename] = cells().exec_counter()

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
            cell_id = request.get('executed_cell_id', None)
            if self.settings.backwards_cell_staleness_propagation:
                order_index_by_id = None
                cells_to_check = cells().all_cells_most_recently_run_for_each_id()
            else:
                order_index_by_id = request['order_index_by_cell_id']
                cells_to_check = (
                    cell for cell in (
                        cells().from_id(cell_id) for cell_id in order_index_by_id
                    ) if cell is not None
                )
            cells().set_cell_positions(order_index_by_id)
            response = self.check_and_link_multiple_cells(cells_to_check=cells_to_check).to_json()
            response['type'] = 'cell_freshness'
            response['exec_mode'] = self.mut_settings.exec_mode.value
            response['last_executed_cell_id'] = cell_id
            response['highlights_enabled'] = self.mut_settings.highlights_enabled
            if comm is not None:
                comm.send(response)
        else:
            dbg_msg = 'Unsupported request type for request %s' % request
            logger.error(dbg_msg)
            self._saved_debug_message = dbg_msg

    def check_and_link_multiple_cells(
        self,
        cells_to_check: Optional[Iterable[ExecutedCodeCell]] = None,
        update_liveness_time_versions: bool = False,
    ) -> FrontendCheckerResult:
        stale_cells = set()
        typecheck_error_cells = set()
        fresh_cells = set()
        new_fresh_cells = set()
        stale_symbols_by_cell_id: Dict[CellId, Set[DataSymbol]] = {}
        killing_cell_ids_for_symbol: Dict[DataSymbol, Set[CellId]] = defaultdict(set)
        phantom_cell_info: Dict[CellId, Dict[CellId, Set[int]]] = {}
        if cells_to_check is None:
            cells_to_check = cells().all_cells_most_recently_run_for_each_id()
        for cell in cells_to_check:
            try:
                checker_result = cell.check_and_resolve_symbols(
                    update_liveness_time_versions=update_liveness_time_versions
                )
            except SyntaxError:
                continue
            cell_id = cell.cell_id
            if cells().position_independent:
                stale_symbols = {sym for sym in checker_result.live if sym.is_stale}
            else:
                stale_symbols = {sym for sym in checker_result.live if sym.is_stale_at_position(cell.position)}
            if len(stale_symbols) > 0:
                stale_symbols_by_cell_id[cell_id] = stale_symbols
                stale_cells.add(cell_id)
            if not checker_result.typechecks:
                typecheck_error_cells.add(cell_id)
            for dead_sym in checker_result.dead:
                killing_cell_ids_for_symbol[dead_sym].add(cell_id)

            if self.settings.mark_phantom_cell_usages_unsafe:
                phantom_cell_info_for_cell = cell.compute_phantom_cell_info(checker_result.used_cells)
                if len(phantom_cell_info_for_cell) > 0:
                    phantom_cell_info[cell_id] = phantom_cell_info_for_cell

            if cell_id not in stale_cells:
                max_timestamp_cell_num = cell.get_max_used_live_symbol_cell_counter(checker_result.live)
                if max_timestamp_cell_num > cell.cell_ctr:
                    fresh_cells.add(cell_id)
                if max_timestamp_cell_num >= cells().exec_counter():
                    new_fresh_cells.add(cell_id)
        stale_links: Dict[CellId, Set[CellId]] = defaultdict(set)
        refresher_links: Dict[CellId, Set[CellId]] = defaultdict(set)
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
                refresher_links[refresher_cell_id].add(stale_cell_id)
        return FrontendCheckerResult(
            # TODO: we should probably have separate fields for stale vs non-typechecking cells,
            #  or at least change the name to a more general "unsafe_cells" or equivalent
            stale_cells=stale_cells | typecheck_error_cells,
            fresh_cells=fresh_cells,
            new_fresh_cells=new_fresh_cells,
            stale_links=stale_links,
            refresher_links=refresher_links,
            phantom_cell_info=phantom_cell_info,
        )

    @staticmethod
    def _get_max_timestamp_cell_num_for_symbols(deep_symbols: Set[DataSymbol], shallow_symbols: Set[DataSymbol]) -> int:
        max_timestamp_cell_num = -1
        for dsym in deep_symbols:
            max_timestamp_cell_num = max(max_timestamp_cell_num, dsym.timestamp.cell_num)
        for dsym in shallow_symbols:
            max_timestamp_cell_num = max(max_timestamp_cell_num, dsym.timestamp_excluding_ns_descendents.cell_num)
        return max_timestamp_cell_num

    def _safety_precheck_cell(self, cell: ExecutedCodeCell) -> None:
        checker_result = self.check_and_link_multiple_cells(
            cells_to_check=[cell],
            update_liveness_time_versions=self.mut_settings.static_slicing_enabled,
        )
        if cell.cell_id in checker_result.stale_cells:
            self.safety_issue_detected = True

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

    async def safe_execute(self, cell_content: str, is_async: bool, run_cell_func):
        if self._saved_debug_message is not None:  # pragma: no cover
            logger.error(self._saved_debug_message)
            self._saved_debug_message = None
        ret = None
        with save_number_of_currently_executing_cell():
            cell_id, self._active_cell_id = self._active_cell_id, None
            assert cell_id is not None
            cell = cells().create_and_track(
                cell_id, cell_content, validate_ipython_counter=self.settings.store_history
            )

            # Stage 1: Precheck.
            self._safety_precheck_cell(cell)

            # Stage 2: Trace / run the cell, updating dependencies as they are encountered.
            try:
                with self._tracing_context(cell_id):
                    if is_async:
                        ret = await run_cell_func(cell_content)  # pragma: no cover
                    else:
                        ret = run_cell_func(cell_content)
                # Stage 2.1: resync any defined symbols that could have gotten out-of-sync
                #  due to tracing being disabled

                self._resync_symbols([
                    # TODO: avoid bad performance by only iterating over symbols updated in this cell
                    sym for sym in self.all_data_symbols() if sym.timestamp.cell_num == cells().exec_counter()
                ])
                self._gc()
            except Exception as e:
                if self.is_test:
                    self.set_exception_raised_during_execution(e)
            finally:
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
        print_ = print  # to keep the test from failing since this is a legitimate print
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
            elif cmd in ('mode', 'exec_mode'):
                return line_magics.set_exec_mode(line)
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
                print_(outstr)
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
        garbage_syms = [dsym for dsym in self.all_data_symbols() if dsym.is_new_garbage()]
        for dsym in garbage_syms:
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
