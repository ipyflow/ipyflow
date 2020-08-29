# -*- coding: utf-8 -*-
import ast
from collections import defaultdict
from contextlib import contextmanager
import inspect
import logging
import re
import sys
from typing import cast, TYPE_CHECKING

import black
from IPython import get_ipython
from IPython.core.magic import register_cell_magic, register_line_magic

from nbsafety.analysis import (
    compute_live_dead_symbol_refs,
    compute_lineno_to_stmt_mapping,
    get_symbols_for_references,
    compute_call_chain_live_symbols,
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
from nbsafety.tracing import AttrSubTracingManager, make_tracer, TraceState
from nbsafety.utils import DotDict

if TYPE_CHECKING:
    from typing import Any, Dict, List, Set, Optional, Tuple, Union
    from nbsafety.data_model.data_symbol import DataSymbol

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
    )


class NotebookSafety(object):
    """Holds all the state necessary to detect stale dependencies in Jupyter notebooks."""
    def __init__(self, cell_magic_name=None, use_comm=False, **kwargs):
        # Note: explicitly adding the types helps PyCharm's built-in code inspection
        self.namespaces: Dict[int, NamespaceScope] = {}
        self.aliases: Dict[int, Set[DataSymbol]] = defaultdict(set)
        self.global_scope: Scope = Scope(self)
        self.updated_symbols: Set[DataSymbol] = set()
        self.updated_scopes: Set[NamespaceScope] = set()
        self.garbage_namespace_obj_ids: Set[int] = set()
        self.statement_cache: Dict[int, Dict[int, ast.stmt]] = {}
        self.statement_to_func_cell: Dict[int, DataSymbol] = {}
        self.trace_event_counter: List[int] = [0]
        self.stale_dependency_detected = False
        self.trace_state: TraceState = TraceState(self)
        self.attr_trace_manager: AttrSubTracingManager = AttrSubTracingManager(
            self, self.global_scope, self.trace_event_counter
        )
        self._last_execution_counter = 0
        self._counters_by_cell_id: Dict[Union[str, int], int] = {}
        self._active_cell_id: Optional[str] = None
        self._save_prev_trace_state_for_tests: bool = kwargs.pop('save_prev_trace_state_for_tests', False)
        if self._save_prev_trace_state_for_tests:
            self.prev_trace_state: Optional[TraceState] = None
        if cell_magic_name is None:
            self._cell_magic = None
        else:
            self._cell_magic = self._make_cell_magic(cell_magic_name)
        self._line_magic = self._make_line_magic()
        self._last_refused_code: Optional[str] = None
        self._prev_cell_stale_symbols: Set[DataSymbol] = set()
        self.config = DotDict(dict(
            store_history=kwargs.pop('store_history', True),
            use_comm=use_comm,
            trace_messages_enabled=kwargs.pop('trace_messages_enabled', False),
            intra_cell_staleness_propagation=True,
            track_dependencies=True,
            skip_unsafe_cells=kwargs.pop('skip_unsafe', True),
            use_new_update_protocol=True,
            mode=kwargs.pop('mode', SafetyRunMode.DEVELOP),
            **kwargs
        ))
        if use_comm:
            get_ipython().kernel.comm_manager.register_target(__package__, self._comm_target)

    @property
    def is_develop(self) -> bool:
        return self.config.get('mode', SafetyRunMode.DEVELOP) == SafetyRunMode.DEVELOP

    def set_active_cell(self, cell_id):
        self._active_cell_id = cell_id

    def _comm_target(self, comm, open_msg):
        @comm.on_msg
        def _responder(msg):
            request = msg['content']['data']
            self.handle(request, comm=comm)

        comm.send({'type': 'establish'})

    def handle(self, request, comm=None):
        if request['type'] == 'change_active_cell':
            self.set_active_cell(request['active_cell_id'])
        elif request['type'] == 'cell_freshness':
            cell_id = request.get('executed_cell_id', None)
            if cell_id is not None:
                self._counters_by_cell_id[cell_id] = self._last_execution_counter
            cells_by_id = request['content_by_cell_id']
            response = self.multicell_precheck(cells_by_id)
            response['type'] = 'cell_freshness'
            if comm is not None:
                comm.send(response)
        else:
            logger.error('Unsupported request type for request %s' % request)

    def multicell_precheck(self, cells_by_id: 'Dict[Union[int, str], str]') -> 'Dict[str, Any]':
        stale_input_cells = []
        stale_output_cells = []
        fresh_cells = []
        stale_symbols_by_cell_id: 'Dict[Union[int, str], Set[DataSymbol]]' = {}
        killing_cell_ids_for_symbol: 'Dict[DataSymbol, Set[Union[int, str]]]' = defaultdict(set)
        for cell_id, cell_content in cells_by_id.items():
            try:
                stale_symbols, dead_symbols, _, max_defined_cell_num = self._precheck_stale_nodes(cell_content)
                if len(stale_symbols) > 0:
                    stale_symbols_by_cell_id[cell_id] = stale_symbols
                    stale_input_cells.append(cell_id)
                else:
                    for dead_sym in dead_symbols:
                        killing_cell_ids_for_symbol[dead_sym].add(cell_id)
                    if max_defined_cell_num > self._counters_by_cell_id.get(cell_id, cast(int, float('inf'))):
                        stale_output_cells.append(cell_id)
                    else:
                        fresh_cells.append(cell_id)
            except SyntaxError:
                continue
        stale_links = defaultdict(list)
        refresher_links = defaultdict(list)
        for stale_cell_id in stale_input_cells:
            stale_syms = stale_symbols_by_cell_id[stale_cell_id]
            refresher_cell_ids = list(set.union(*(killing_cell_ids_for_symbol[stale_sym] for stale_sym in stale_syms)))
            stale_links[stale_cell_id] = refresher_cell_ids
            for refresher_cell_id in refresher_cell_ids:
                refresher_links[refresher_cell_id].append(stale_cell_id)
        return {
            'stale_input_cells': stale_input_cells,
            'stale_output_cells': stale_output_cells,
            'stale_links': stale_links,
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

    def _precheck_stale_nodes(self, cell: 'Union[ast.Module, str]') -> 'Tuple[Set[DataSymbol], Set[DataSymbol], Set[DataSymbol], int]':
        if isinstance(cell, str):
            cell = self._get_cell_ast(cell)
        max_defined_cell_num = -1
        live_symbol_refs, dead_symbol_refs = compute_live_dead_symbol_refs(cell)
        live_symbols, called_symbols = get_symbols_for_references(live_symbol_refs, self.global_scope)
        live_symbols = live_symbols.union(compute_call_chain_live_symbols(called_symbols))
        dead_symbols, _ = get_symbols_for_references(dead_symbol_refs, self.global_scope)
        stale_symbols = set()
        for dsym in live_symbols:
            max_defined_cell_num = max(max_defined_cell_num, dsym.defined_cell_num)
            if dsym.is_stale:
                stale_symbols.add(dsym)
            if dsym.obj_id in self.namespaces:
                namespace_scope = self.namespaces[dsym.obj_id]
                max_defined_cell_num = max(max_defined_cell_num, namespace_scope.max_defined_timestamp)
        return stale_symbols, dead_symbols, live_symbols, max_defined_cell_num

    def _precheck_simple(self, cell):
        return len(self._precheck_stale_nodes(cell)[0]) > 0

    def _precheck_for_stale(self, cell: str):
        # Precheck process. First obtain the names that need to be checked. Then we check if their
        # `defined_cell_num` is greater than or equal to required; if not we give a warning and return `True`.
        try:
            cell_ast = self._get_cell_ast(cell)
        except SyntaxError:
            return False
        self.statement_cache[cell_counter()] = compute_lineno_to_stmt_mapping(cell_ast)
        stale_symbols, _, live_symbols, _ = self._precheck_stale_nodes(cell_ast)
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
        self._resync_symbols(live_symbols)
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
            # self.aliases[dsym.obj_id].discard(dsym)
            # self.aliases[id(obj)].add(dsym)
            self.aliases[id(obj)] = self.aliases[dsym.obj_id]
            del self.aliases[dsym.obj_id]
            namespace = self.namespaces.get(dsym.obj_id, None)
            if namespace is not None:
                namespace.update_obj_ref(obj)
                del self.namespaces[dsym.obj_id]
                self.namespaces[id(obj)] = namespace
            dsym.update_obj_ref(obj)

    def safe_execute(self, cell: str, run_cell_func):
        try:
            cell = black.format_file_contents(cell, fast=False, mode=black.FileMode())
        except:  # noqa
            pass

        self.attr_trace_manager.ast_transformer.skip_lines.clear()
        with save_number_of_currently_executing_cell():
            self._last_execution_counter = cell_counter()

            for lineno, line in enumerate(cell.strip().split('\n')):
                if _NB_MAGIC_PATTERN.search(line) is not None:
                    self.attr_trace_manager.ast_transformer.skip_lines.add(lineno + 1)

            if self._active_cell_id is not None:
                self._counters_by_cell_id[self._active_cell_id] = self._last_execution_counter
                self._active_cell_id = None
            # Stage 1: Precheck.
            if self._precheck_for_stale(cell) and self.config.get('skip_unsafe_cells', True):
                # FIXME: hack to increase cell number
                #  ideally we shouldn't show a cell number at all if we fail precheck since nothing executed
                return run_cell_func('None')

            def _backup():
                # something went wrong silently (e.g. due to line magic); fall back to just executing the code
                logger.warning('Something failed while attempting traced execution; '
                               'falling back to uninstrumented execution.')
                return run_cell_func(cell)

            # Stage 2: Trace / run the cell, updating dependencies as they are encountered.
            try:
                with self._tracing_context():
                    ret = run_cell_func(cell)
            finally:
                if self.trace_state.error_occurred:
                    ret = _backup()
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
        sys.settrace(make_tracer(self))
        try:
            with ast_transformer_context(self.attr_trace_manager.ast_transformer):
                yield
        finally:
            sys.settrace(None)
            # TODO: actually handle errors that occurred in our code while tracing
            # if not self.trace_state.error_occurred:
            self._reset_trace_state_hook()
            if self.config.get('use_new_update_protocol', False):
                return
            for updated_symbol in self.updated_symbols:
                updated_symbol.refresh()
            for updated_scope in self.updated_scopes:
                updated_scope.refresh()

    def _reset_trace_state_hook(self):
        if self.dependency_tracking_enabled and self.trace_state.prev_trace_stmt_in_cur_frame is not None:
            self.trace_state.prev_trace_stmt_in_cur_frame.finished_execution_hook()
        assert len(self.attr_trace_manager.stack) == 0
        self.attr_trace_manager.reset()  # should happen on finish_execution_hook, but since its idempotent do it again
        if self._save_prev_trace_state_for_tests:
            self.prev_trace_state = self.trace_state
        self.trace_state = TraceState(self)
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
            elif line[0] == "set_propagation":
                return line_magics.set_propagation(self, line)
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
        return self.config.track_dependencies

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
            self.namespaces.pop(obj_id, None)
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
