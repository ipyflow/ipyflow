# -*- coding: utf-8 -*-
import ast
from collections import defaultdict
from contextlib import contextmanager
import inspect
import logging
import re
from typing import cast, TYPE_CHECKING

from IPython import get_ipython
from IPython.core.magic import register_cell_magic, register_line_magic

from nbsafety.analysis import (
    compute_live_dead_symbol_refs,
    compute_call_chain_live_symbols,
    get_symbols_for_references,
)
from nbsafety.ipython_utils import (
    ast_transformer_context,
    cell_counter,
    run_cell,
    save_number_of_currently_executing_cell,
)
from nbsafety import line_magics
from nbsafety.data_model.scope import Scope, NamespaceScope
from nbsafety.run_mode import SafetyRunMode
from nbsafety.tracing import SafetyAstRewriter, TracingManager
from nbsafety.utils import DotDict

if TYPE_CHECKING:
    from typing import Any, Dict, List, Set, Optional, Tuple, Union
    from types import FrameType
    from nbsafety.data_model.data_symbol import DataSymbol
    CellId = Union[str, int]

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

_MAX_WARNINGS = 10
_SAFETY_LINE_MAGIC = 'safety'

_NB_MAGIC_PATTERN = re.compile(r'(^%|^!|^cd |\?$)')


def _safety_warning(node: 'DataSymbol'):
    if not node.is_stale:
        raise ValueError('Expected node with stale ancestor; got %s' % node)
    if node.defined_cell_num < 1:
        return
    fresher_symbols = node.fresher_ancestors
    if len(fresher_symbols) == 0:
        fresher_symbols = node.namespace_stale_symbols
    logger.warning(
        f'`{node.readable_name}` defined in cell {node.defined_cell_num} may depend on '
        f'old version(s) of [{", ".join(f"`{str(dep)}`" for dep in fresher_symbols)}] '
        f'(latest update in cell {node.required_cell_num}).'
        f'\n\n(Run cell again to override and execute anyway.)'
    )


class NotebookSafety(object):
    """Holds all the state necessary to detect stale dependencies in Jupyter notebooks."""
    def __init__(self, cell_magic_name=None, use_comm=False, **kwargs):
        self.config = DotDict(dict(
            store_history=kwargs.pop('store_history', True),
            test_context=kwargs.pop('test_context', False),
            use_comm=use_comm,
            trace_messages_enabled=kwargs.pop('trace_messages_enabled', False),
            backwards_cell_staleness_propagation=True,
            track_dependencies=True,
            naive_refresher_computation=False,
            skip_unsafe_cells=kwargs.pop('skip_unsafe', True),
            mode=kwargs.pop('mode', SafetyRunMode.DEVELOP),
            **kwargs
        ))
        # Note: explicitly adding the types helps PyCharm's built-in code inspection
        self.namespaces: 'Dict[int, NamespaceScope]' = {}
        self.aliases: 'Dict[int, Set[DataSymbol]]' = defaultdict(set)
        self.global_scope: 'Scope' = Scope(self)
        self.updated_symbols: 'Set[DataSymbol]' = set()
        self.updated_scopes: 'Set[NamespaceScope]' = set()
        self.garbage_namespace_obj_ids: 'Set[int]' = set()
        self.ast_node_by_id: 'Dict[int, ast.AST]' = {}
        self.statement_cache: 'Dict[int, Dict[int, ast.stmt]]' = defaultdict(dict)
        self.statement_to_func_cell: 'Dict[int, DataSymbol]' = {}
        self.tracing_manager: 'TracingManager' = TracingManager(self)
        self.stale_dependency_detected = False
        self.active_cell_position_idx = -1
        self._last_execution_counter = 0
        self._counters_by_cell_id: Dict[CellId, int] = {}
        self._active_cell_id: Optional[str] = None
        if cell_magic_name is None:
            self._cell_magic = None
        else:
            self._cell_magic = self._make_cell_magic(cell_magic_name)
        # self._line_magic = self._make_line_magic()
        self._last_refused_code: Optional[str] = None
        self._prev_cell_stale_symbols: Set[DataSymbol] = set()
        self._cell_counter = 1
        self._recorded_cell_name_to_cell_num = True
        self._cell_name_to_cell_num_mapping: 'Dict[str, int]' = {}
        self._ast_transformer_raised: 'Optional[Exception]' = None
        if use_comm:
            get_ipython().kernel.comm_manager.register_target(__package__, self._comm_target)

    @property
    def is_develop(self) -> bool:
        return self.config.get('mode', SafetyRunMode.DEVELOP) == SafetyRunMode.DEVELOP

    @property
    def is_test(self) -> bool:
        return self.config.get('test_context', False)

    def cell_counter(self):
        if self.config.store_history:
            return cell_counter()
        else:
            return self._cell_counter

    def set_ast_transformer_raised(self, new_val: 'Optional[Exception]' = None) -> 'Optional[Exception]':
        ret = self._ast_transformer_raised
        self._ast_transformer_raised = new_val
        return ret

    def get_position(self, frame: 'FrameType'):
        cell_num = self._cell_name_to_cell_num_mapping[frame.f_code.co_filename.split('-')[3]]
        return cell_num, frame.f_lineno

    def maybe_set_name_to_cell_num_mapping(self, frame: 'FrameType'):
        if self._recorded_cell_name_to_cell_num:
            return
        self._recorded_cell_name_to_cell_num = True
        self._cell_name_to_cell_num_mapping[frame.f_code.co_filename.split('-')[3]] = self.cell_counter()

    def set_active_cell(self, cell_id, position_idx=-1):
        self._active_cell_id = cell_id
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
            cell_id = request.get('executed_cell_id', None)
            if cell_id is not None:
                self._counters_by_cell_id[cell_id] = self._last_execution_counter
            cells_by_id = request['content_by_cell_id']
            if self.config.get('backwards_cell_staleness_propagation', True):
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
            cells_by_id: 'Dict[CellId, str]',
            order_index_by_cell_id: 'Optional[Dict[CellId, int]]' = None
    ) -> 'Dict[str, Any]':
        stale_cells = set()
        fresh_cells = []
        stale_symbols_by_cell_id: 'Dict[CellId, Set[DataSymbol]]' = {}
        killing_cell_ids_for_symbol: 'Dict[DataSymbol, Set[CellId]]' = defaultdict(set)
        for cell_id, cell_content in cells_by_id.items():
            if (order_index_by_cell_id is not None and
                    order_index_by_cell_id.get(cell_id, -1) <= self.active_cell_position_idx):
                continue
            try:
                symbols = self._check_cell_and_resolve_symbols(cell_content)
                stale_symbols, dead_symbols = symbols['stale'], symbols['dead']
                if len(stale_symbols) > 0:
                    stale_symbols_by_cell_id[cell_id] = stale_symbols
                    stale_cells.add(cell_id)
                elif (self._get_max_defined_cell_num_for_symbols(symbols['live']) >
                      self._counters_by_cell_id.get(cell_id, cast(int, float('inf')))):
                    fresh_cells.append(cell_id)
                for dead_sym in dead_symbols:
                    killing_cell_ids_for_symbol[dead_sym].add(cell_id)
            except SyntaxError:
                continue
        stale_links: 'Dict[CellId, Set[CellId]]' = defaultdict(set)
        refresher_links: 'Dict[CellId, List[CellId]]' = defaultdict(list)
        for stale_cell_id in stale_cells:
            stale_syms = stale_symbols_by_cell_id[stale_cell_id]
            if self.config.get('naive_refresher_computation', False):
                refresher_cell_ids = self._naive_compute_refresher_cells(
                    stale_cell_id,
                    stale_syms,
                    cells_by_id,
                    order_index_by_cell_id=order_index_by_cell_id
                )
            else:
                refresher_cell_ids = set.union(*(killing_cell_ids_for_symbol[stale_sym] for stale_sym in stale_syms))
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
        return {
            'stale_cells': list(stale_cells),
            'fresh_cells': fresh_cells,
            'stale_links': {
                stale_cell_id: list(refresher_cell_ids)
                for stale_cell_id, refresher_cell_ids in stale_links.items()
            },
            'refresher_links': refresher_links,
        }

    def _naive_compute_refresher_cells(
            self,
            stale_cell_id: 'CellId',
            stale_symbols: 'Set[DataSymbol]',
            cells_by_id: 'Dict[CellId, str]',
            order_index_by_cell_id: 'Optional[Dict[CellId, int]]' = None
    ) -> 'Set[CellId]':
        refresher_cell_ids: Set[CellId] = set()
        stale_cell_content = cells_by_id[stale_cell_id]
        for cell_id, cell_content in cells_by_id.items():
            if cell_id == stale_cell_id:
                continue
            if (order_index_by_cell_id is not None and
                    order_index_by_cell_id.get(cell_id, -1) >= order_index_by_cell_id.get(stale_cell_id, -1)):
                continue
            concated_content = f'{cell_content}\n\n{stale_cell_content}'
            try:
                concated_stale_symbols = self._check_cell_and_resolve_symbols(concated_content)['stale']
            except SyntaxError:
                continue
            if concated_stale_symbols < stale_symbols:
                refresher_cell_ids.add(cell_id)
        return refresher_cell_ids

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

    def _get_max_defined_cell_num_for_symbols(self, symbols: 'Set[DataSymbol]') -> int:
        max_defined_cell_num = -1
        for dsym in symbols:
            max_defined_cell_num = max(max_defined_cell_num, dsym.defined_cell_num)
            if dsym.obj_id in self.namespaces:
                namespace_scope = self.namespaces[dsym.obj_id]
                max_defined_cell_num = max(max_defined_cell_num, namespace_scope.max_defined_timestamp)
        return max_defined_cell_num

    def _check_cell_and_resolve_symbols(
            self,
            cell: 'Union[ast.Module, str]'
    ) -> 'Dict[str, Set[DataSymbol]]':
        if isinstance(cell, str):
            cell = self._get_cell_ast(cell)
        live_symbol_refs, dead_symbol_refs = compute_live_dead_symbol_refs(cell)
        live_symbols, called_symbols = get_symbols_for_references(live_symbol_refs, self.global_scope)
        live_symbols = live_symbols.union(compute_call_chain_live_symbols(called_symbols))
        # only mark dead attrsubs as killed if we can traverse the entire chain
        dead_symbols, _ = get_symbols_for_references(
            dead_symbol_refs, self.global_scope, only_add_successful_resolutions=True
        )
        stale_symbols = set(dsym for dsym in live_symbols if dsym.is_stale)
        return {
            'live': live_symbols,
            'dead': dead_symbols,
            'stale': stale_symbols,
        }

    def _precheck_for_stale(self, cell: str):
        # Precheck process. First obtain the names that need to be checked. Then we check if their
        # `defined_cell_num` is greater than or equal to required; if not we give a warning and return `True`.
        try:
            cell_ast = self._get_cell_ast(cell)
        except SyntaxError:
            return False
        symbols = self._check_cell_and_resolve_symbols(cell_ast)
        stale_symbols, live_symbols = symbols['stale'], symbols['live']
        if self._last_refused_code is None or cell != self._last_refused_code:
            self._prev_cell_stale_symbols = stale_symbols
            if len(stale_symbols) > 0:
                warning_counter = 0
                for node in self._prev_cell_stale_symbols:
                    if warning_counter >= _MAX_WARNINGS:
                        logger.warning(f'{len(self._prev_cell_stale_symbols) - warning_counter}'
                                       ' more nodes with stale dependencies skipped...')
                        break
                    _safety_warning(node)
                    warning_counter += 1
                self.stale_dependency_detected = True
                self._last_refused_code = cell
                return True
        else:
            # Instead of breaking the dependency chain, simply refresh the nodes
            # with stale deps to their required cell numbers
            for node in self._prev_cell_stale_symbols:
                node.defined_cell_num = node.required_cell_num
                node.namespace_stale_symbols = set()
                node.fresher_ancestors = set()
            self._prev_cell_stale_symbols.clear()

        self._last_refused_code = None
        return False

    def _resync_symbols(self, symbols: 'Set[DataSymbol]'):
        for dsym in symbols:
            if not dsym.containing_scope.is_global:
                continue
            obj = get_ipython().user_global_ns.get(dsym.name, None)
            if obj is None:
                continue
            if dsym.obj_id == id(obj):
                continue
            for alias in self.aliases[dsym.cached_obj_id] | self.aliases[dsym.obj_id]:
                if not alias.containing_scope.is_namespace_scope:
                    continue
                containing_scope = cast(NamespaceScope, alias.containing_scope)
                if containing_scope._obj_ref is None or containing_scope._obj_ref() is None:
                    continue
                containing_obj = containing_scope._obj_ref()
                # TODO: handle dict case too
                if isinstance(containing_obj, list) and containing_obj[-1] is obj:
                    # new_alias_sym = containing_scope.upsert_data_symbol_for_name(
                    containing_scope.upsert_data_symbol_for_name(
                        len(containing_obj) - 1,
                        obj,
                        set(alias.parents),
                        alias.stmt_node,
                        is_subscript=True,
                        propagate=False
                    )
                    # self.aliases[id(obj)].add(new_alias_sym)
            self.aliases[dsym.cached_obj_id].discard(dsym)
            self.aliases[dsym.obj_id].discard(dsym)
            self.aliases[id(obj)].add(dsym)
            namespace = self.namespaces.get(dsym.obj_id, None)
            if namespace is not None:
                namespace.update_obj_ref(obj)
                del self.namespaces[dsym.obj_id]
                self.namespaces[id(obj)] = namespace
            dsym.update_obj_ref(obj)

    def safe_execute(self, cell: str, run_cell_func):
        with save_number_of_currently_executing_cell():
            self._last_execution_counter = self.cell_counter()

            if self._active_cell_id is not None:
                self._counters_by_cell_id[self._active_cell_id] = self._last_execution_counter
                self._active_cell_id = None
            # Stage 1: Precheck.
            if self._precheck_for_stale(cell) and self.config.get('skip_unsafe_cells', True):
                # FIXME: hack to increase cell number
                #  ideally we shouldn't show a cell number at all if we fail precheck since nothing executed
                return run_cell_func('None')

            # Stage 2: Trace / run the cell, updating dependencies as they are encountered.
            try:
                with self._tracing_context():
                    ret = run_cell_func(cell)
                # Stage 2.1: resync any defined symbols that could have gotten out-of-sync
                #  due to tracing being disabled
                defined = self._check_cell_and_resolve_symbols(cell)['dead']
                self._resync_symbols(defined)
            finally:
                if not self.config.store_history:
                    self._cell_counter += 1
                return ret

    def _make_cell_magic(self, cell_magic_name):
        def _run_cell_func(cell):
            run_cell(cell, store_history=self.config.store_history)

        def _dependency_safety(_, cell: str):
            self.safe_execute(cell, _run_cell_func)

        # FIXME (smacke): probably not a great idea to rely on this
        _dependency_safety.__name__ = cell_magic_name
        return register_cell_magic(_dependency_safety)

    @contextmanager
    def _tracing_context(self):
        self.updated_symbols.clear()
        self.updated_scopes.clear()
        self._recorded_cell_name_to_cell_num = False

        try:
            with self.tracing_manager.tracing_context():
                with ast_transformer_context([SafetyAstRewriter(self)]):
                    yield
        finally:
            # TODO: actually handle errors that occurred in our code while tracing
            # if not self.trace_state_manager.error_occurred:
            self._reset_trace_state_hook()

    def _reset_trace_state_hook(self):
        # this assert doesn't hold anymore now that tracing could be disabled inside of something
        # assert len(self.attr_trace_manager.stack) == 0
        self.tracing_manager = TracingManager(self)
        self._gc()

    def _make_line_magic(self):
        line_magic_names = [f[0] for f in inspect.getmembers(line_magics) if inspect.isfunction(f[1])]

        def _safety(line_: str):
            line = line_.split()
            if not line or line[0] not in line_magic_names:
                print(line_magics.USAGE)
                return
            elif line[0] in ("show_deps", "show_dependency", "show_dependencies"):
                return line_magics.show_deps(self, line)
            elif line[0] == "show_stale":
                return line_magics.show_stale(self, line)
            elif line[0] == "trace_messages":
                return line_magics.trace_messages(self, line)
            elif line[0] == "remove_dependency":
                return line_magics.remove_dep(self, line)
            elif line[0] in ("add_dependency", "add_dep"):
                return line_magics.add_dep(self, line)
            elif line[0] == "turn_off_warnings_for":
                return line_magics.turn_off_warnings_for(self, line)
            elif line[0] == "turn_on_warnings_for":
                return line_magics.turn_on_warnings_for(self, line)

        # FIXME (smacke): probably not a great idea to rely on this
        _safety.__name__ = _SAFETY_LINE_MAGIC
        return register_line_magic(_safety)

    @property
    def dependency_tracking_enabled(self):
        return self.config.get('track_dependencies', True)

    @property
    def cell_magic_name(self):
        return self._cell_magic.__name__

    @property
    def line_magic_name(self):
        return self._line_magic.__name__

    def all_data_symbols(self):
        for alias_set in self.aliases.values():
            for alias in alias_set:
                yield alias

    def test_and_clear_detected_flag(self):
        ret = self.stale_dependency_detected
        self.stale_dependency_detected = False
        return ret

    def _namespace_gc(self):
        for obj_id in self.garbage_namespace_obj_ids:
            garbage_ns = self.namespaces.pop(obj_id, None)
            if garbage_ns is not None:
                garbage_ns.clear_namespace(obj_id)
        self.garbage_namespace_obj_ids.clear()
        # while True:
        #     for obj_id in self.garbage_namespace_obj_ids:
        #         self.namespaces.pop(obj_id, None)
        #     self.garbage_namespace_obj_ids.clear()
        #     for obj_id, namespace in self.namespaces.items():
        #         if namespace.is_garbage:
        #             self.garbage_namespace_obj_ids.add(namespace.obj_id)
        #     if len(self.garbage_namespace_obj_ids) == 0:
        #         break

    def _gc(self):
        for dsym in list(self.all_data_symbols()):
            if dsym.is_garbage:
                dsym.collect_self_garbage()

    def retrieve_namespace_attr_or_sub(self, obj: 'Any', attr_or_sub: 'Union[str, int]', is_subscript: bool):
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
