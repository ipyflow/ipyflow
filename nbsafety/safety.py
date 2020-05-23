# -*- coding: utf-8 -*-
import ast
from collections import defaultdict
from contextlib import contextmanager
import logging
import sys
from typing import cast, TYPE_CHECKING

from IPython import get_ipython
from IPython.core.magic import register_cell_magic, register_line_magic

from .analysis import AttrSubSymbolChain, ComputeReachableSymbolRefs, compute_lineno_to_stmt_mapping
from .ipython_utils import (
    ast_transformer_context,
    cell_counter,
    run_cell,
    save_number_of_currently_executing_cell,
)
from . import line_magics
from .scope import Scope, NamespaceScope
from .tracing import AttributeTracingManager, make_tracer, TraceState

if TYPE_CHECKING:
    from typing import Dict, List, Set, Optional, Tuple, Union
    from .analysis import SymbolRef
    from .data_cell import DataCell

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

_MAX_WARNINGS = 10
_SAFETY_LINE_MAGIC = 'safety'


def _safety_warning(name: str, defined_cell_num: int, required_cell_num: int, fresher_ancestors: 'Set[DataCell]'):
    logger.warning(
        f'`{name}` defined in cell {defined_cell_num} may depend on '
        f'old version(s) of [{", ".join(f"`{str(dep)}`" for dep in fresher_ancestors)}] '
        f'(latest update in cell {required_cell_num}).'
    )


class DependencySafety(object):
    """Holds all the state necessary to detect stale dependencies in Jupyter notebooks."""
    def __init__(self, cell_magic_name=None, **kwargs):
        self.global_scope = Scope()
        self.namespaces: Dict[int, NamespaceScope] = {}
        self.aliases: Dict[int, Set[DataCell]] = defaultdict(set)
        self.statement_cache: Dict[int, Dict[int, ast.stmt]] = {}
        self.trace_event_counter = [0]
        self.stale_dependency_detected = False
        self.trace_state: TraceState = TraceState(self)
        self.attr_trace_manager: AttributeTracingManager = AttributeTracingManager(
            self.namespaces, self.aliases, self.global_scope, self.trace_event_counter
        )
        self.store_history = kwargs.pop('store_history', True)
        self.use_comm = kwargs.pop('use_comm', False)
        self.trace_messages_enabled = kwargs.pop('trace_messages_enabled', False)
        self._last_execution_counter: int = 0
        self._counters_by_cell_id: Dict[str, int] = {}
        self._save_prev_trace_state_for_tests = kwargs.pop('save_prev_trace_state_for_tests', False)
        if self._save_prev_trace_state_for_tests:
            self.prev_trace_state: Optional[TraceState] = None
        self._cell_magic = self._make_cell_magic(cell_magic_name)
        # Maybe switch update this too when you are implementing the usage of cell_magic_name?
        self._line_magic = self._make_line_magic()
        self._last_refused_code: Optional[str] = None
        self._track_dependencies = True

        self._disable_level = 0
        self._prev_cell_nodes_with_stale_deps: Set[DataCell] = set()

        if self.use_comm:
            get_ipython().kernel.comm_manager.register_target('nbsafety', self._comm_target)

    def _comm_target(self, comm, open_msg):
        @comm.on_msg
        def _responder(msg):
            cell_id = msg['content']['data']['executed_cell_id']
            self._counters_by_cell_id[cell_id] = self._last_execution_counter
            tasks = msg['content']['data']['content_by_cell_id']
            stale_input_cells = []
            stale_output_cells = []
            fresh_cells = []
            for cell_id, cell_content in tasks.items():
                try:
                    stale_nodes, max_defined_cell_num = self._precheck_stale_nodes(cell_content)
                    if len(stale_nodes) > 0:
                        stale_input_cells.append(cell_id)
                    elif max_defined_cell_num > self._counters_by_cell_id.get(cell_id, float('inf')):
                        stale_output_cells.append(cell_id)
                    else:
                        fresh_cells.append(cell_id)
                except SyntaxError:
                    continue
            stale_links = defaultdict(list)
            refresher_links = defaultdict(list)
            for candidate_refresher_cell_id in fresh_cells + stale_output_cells:
                candidate_refresher_cell = tasks[candidate_refresher_cell_id]
                for stale_cell_id in stale_input_cells:
                    try:
                        if not self._precheck_simple(f'{candidate_refresher_cell}\n{tasks[stale_cell_id]}'):
                            stale_links[stale_cell_id].append(candidate_refresher_cell_id)
                            refresher_links[candidate_refresher_cell_id].append(stale_cell_id)
                    except SyntaxError:
                        continue
            comm.send({
                'type': 'cell_freshness',
                'stale_input_cells': stale_input_cells,
                'stale_output_cells': stale_output_cells,
                'stale_links': stale_links,
                'refresher_links': refresher_links,
            })

        comm.send({'type': 'establish'})

    def _logging_inited(self):
        self.store_history = True
        logger.setLevel(logging.WARNING)

    @staticmethod
    def _get_cell_ast(cell):
        return ast.parse('\n'.join([
            line for line in cell.strip().split('\n')
            if (
                    # TODO: figure out more robust strategy to strip these out
                    not line.startswith('%')
                    and not line.startswith('!')
                    and not line.startswith('cd')
                    and not line.endswith('?')
            )
        ]))

    def compute_reachable_symbol_refs(self, code: 'Union[ast.Module, str]') -> 'Set[SymbolRef]':
        if isinstance(code, str):
            code = ast.parse(code)
        return ComputeReachableSymbolRefs(self)(code)

    def _precheck_stale_nodes(self, cell: 'Union[ast.Module, str]'):
        if isinstance(cell, str):
            cell = self._get_cell_ast(cell)
        stale_nodes = set()
        max_defined_cell_num = -1
        for symbol_ref in self.compute_reachable_symbol_refs(cell):
            if isinstance(symbol_ref.symbol, str):
                nodes: List[Tuple[DataCell, bool]] = [(
                    self.global_scope.lookup_data_cell_by_name_this_indentation(symbol_ref.symbol),
                    symbol_ref.deep
                )]
            elif isinstance(symbol_ref.symbol, AttrSubSymbolChain):
                nodes = self.global_scope.gen_data_cells_for_attr_symbol_chain(symbol_ref, self.namespaces)
            else:
                logger.warning('invalid type for ref %s', symbol_ref)
                continue
            for node, deep_ref in nodes:
                if node is not None:
                    max_defined_cell_num = max(max_defined_cell_num, node.defined_cell_num)
                    if node.has_stale_ancestor:
                        stale_nodes.add(node)
                    if deep_ref and node.obj_id in self.namespaces:
                        namespace_scope = self.namespaces[node.obj_id]
                        max_defined_cell_num = max(max_defined_cell_num, namespace_scope.max_defined_timestamp)
                        if namespace_scope.has_data_cells_with_stale_ancestors_deep or node.has_stale_ancestor:
                            stale_nodes.add(node)
        return stale_nodes, max_defined_cell_num

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
        if self._last_refused_code is None or cell != self._last_refused_code:
            self._prev_cell_nodes_with_stale_deps, _ = self._precheck_stale_nodes(cell_ast)
            if len(self._prev_cell_nodes_with_stale_deps) > 0 and self._disable_level < 2:
                warning_counter = 0
                for node in self._prev_cell_nodes_with_stale_deps:
                    if warning_counter >= _MAX_WARNINGS:
                        logger.warning(str(len(self._prev_cell_nodes_with_stale_deps) - warning_counter) +
                                       " more nodes with stale dependencies skipped...")
                        break
                    _safety_warning(
                        node.readable_name, node.defined_cell_num, node.required_cell_num, node.fresher_ancestors
                    )
                    warning_counter += 1
                self.stale_dependency_detected = True
                self._last_refused_code = cell
                if self._disable_level == 0:
                    return True
        else:
            # Instead of breaking the dependency chain, simply refresh the nodes
            # with stale deps to their required cell numbers
            for node in self._prev_cell_nodes_with_stale_deps:
                node.defined_cell_num = node.required_cell_num
                node.fresher_ancestors = set()
            self._prev_cell_nodes_with_stale_deps.clear()

        self._last_refused_code = None
        return False

    def _make_cell_magic(self, cell_magic_name):
        def _dependency_safety(_, cell: str):
            if self._disable_level == 3:
                run_cell(cell)
                return

            with save_number_of_currently_executing_cell():
                self._last_execution_counter = cell_counter()
                # Stage 1: Precheck.
                if self._precheck_for_stale(cell):
                    # FIXME: hack to increase cell number
                    # ideally we won't show a cell number at all if we fail precheck since nothing executed
                    run_cell('None', store_history=self.store_history)
                    return

                def _backup():
                    # something went wrong silently (e.g. due to line magic); fall back to just executing the code
                    logger.warning('Something failed while attempting traced execution; '
                                   'falling back to uninstrumented execution.')
                    run_cell(cell, store_history=self.store_history)

                # Stage 2: Trace / run the cell, updating dependencies as they are encountered.
                with self._tracing_context(untraced_backup=_backup):
                    run_cell(cell, store_history=self.store_history)

        if cell_magic_name is not None:
            # TODO (smacke): probably not a great idea to rely on this
            _dependency_safety.__name__ = cell_magic_name
        return register_cell_magic(_dependency_safety)

    @contextmanager
    def _tracing_context(self, untraced_backup=None):
        sys.settrace(make_tracer(self))
        try:
            with ast_transformer_context(self.attr_trace_manager.ast_transformer):
                yield
        finally:
            sys.settrace(None)
            # TODO: actually handle errors that occurred in our code while tracing
            if self.trace_state.error_occurred:
                if untraced_backup is not None:
                    untraced_backup()
            else:
                self._reset_trace_state_hook()

    def _reset_trace_state_hook(self):
        if self.dependency_tracking_enabled and self.trace_state.prev_trace_stmt_in_cur_frame is not None:
            self.trace_state.prev_trace_stmt_in_cur_frame.finished_execution_hook()
        self.attr_trace_manager.reset()  # should happen on finish_execution_hook, but since its idempotent do it again
        if self._save_prev_trace_state_for_tests:
            self.prev_trace_state = self.trace_state
        self.trace_state = TraceState(self)

    def _make_line_magic(self):
        def _safety(line_: str):
            line = line_.split()
            if not line or line[0] not in [
                "show_graph", "show_dependency", "show_stale", "set_disable_level", "trace_messages",
                "remove_dependency", "add_dependency", "turn_off_warnings_for", "turn_on_warnings_for",
            ]:
                print(line_magics.USAGE)
                return
            if line[0] == "show_graph":
                return line_magics.show_graph(self)
            elif line[0] == "show_dependency":
                return line_magics.show_deps(self, line)
            elif line[0] == "show_stale":
                return line_magics.show_stale(self)
            elif line[0] == "set_disable_level":
                return line_magics.set_disable_level(self, line)
            elif line[0] == "trace_messages":
                return line_magics.configure_trace_messages(self, line)
            elif line[0] == "remove_dependency":
                return line_magics.remove_dep(self, line)
            elif line[0] == "add_dependency":
                return line_magics.add_dep(self, line)
            elif line[0] == "turn_off_warnings_for":
                return line_magics.turn_off_warnings_for(self, line)
            elif line[0] == "turn_on_warnings_for":
                return line_magics.turn_on_warnings_for(self, line)

        # TODO (smacke): probably not a great idea to rely on this
        _safety.__name__ = _SAFETY_LINE_MAGIC
        return register_line_magic(_safety)

    @property
    def dependency_tracking_enabled(self):
        return self._track_dependencies

    @property
    def cell_magic_name(self):
        return self._cell_magic.__name__

    @property
    def line_magic_name(self):
        return self._line_magic.__name__

    def test_and_clear_detected_flag(self):
        ret = self.stale_dependency_detected
        self.stale_dependency_detected = False
        return ret
