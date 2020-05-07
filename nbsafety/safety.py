# -*- coding: utf-8 -*-
import ast
from contextlib import contextmanager
import logging
import sys
from typing import TYPE_CHECKING

from IPython.core.magic import register_cell_magic, register_line_magic

from .analysis import AttributeSymbolChain, precheck, compute_lineno_to_stmt_mapping
from .ipython_utils import (
    ast_transformer_context,
    cell_counter,
    run_cell,
    save_number_of_currently_executing_cell,
)
from . import line_magics
from .scope import Scope
from .tracing import AttributeTracingManager, make_tracer, TraceState

if TYPE_CHECKING:
    from typing import Dict, Set, Optional
    from .data_cell import DataCell

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def _safety_warning(name: str, defined_cell_num: int, required_cell_num: int, fresher_ancestors: 'Set[DataCell]'):
    logger.warning(
        f'{name} defined in cell {defined_cell_num} may depend on '
        f'old version(s) of [{", ".join(str(dep) for dep in fresher_ancestors)}] '
        f'(lastest update in cell {required_cell_num}).'
    )


class DependencySafety(object):
    """Holds all the state necessary to detect stale dependencies in Jupyter notebooks."""
    def __init__(self, cell_magic_name=None, line_magic_name=None, **kwargs):
        self.global_scope = Scope()
        self.namespaces: Dict[int, Scope] = {}
        self.statement_cache: Dict[int, Dict[int, ast.stmt]] = {}
        self.stale_dependency_detected = False
        self.trace_state = TraceState(self)
        self.attr_trace_manager = AttributeTracingManager(self.namespaces, self.global_scope)
        self.store_history = kwargs.pop('store_history', True)
        self.trace_messages_enabled = kwargs.pop('trace_messages_enabled', False)
        self._save_prev_trace_state_for_tests = kwargs.pop('save_prev_trace_state_for_tests', False)
        if self._save_prev_trace_state_for_tests:
            self.prev_trace_state: Optional[TraceState] = None
        self._cell_magic = self._make_cell_magic(cell_magic_name)
        # Maybe switch update this too when you are implementing the usage of cell_magic_name?
        self._line_magic = self._make_line_magic(line_magic_name)
        self._last_refused_code: Optional[str] = None
        self._last_cell_ast: Optional[ast.Module] = None
        self._track_dependencies = True

        self._disable_level = 0

    def _logging_inited(self):
        self.store_history = True
        logger.setLevel(logging.WARNING)

    def _precheck_for_stale(self, cell):
        # Precheck process. First obtain the names that need to be checked. Then we check if their
        # `defined_cell_num` is greater than or equal to required; if not we give a warning and return `True`.
        self._last_cell_ast = ast.parse('\n'.join(
            [line for line in cell.strip().split('\n') if not line.startswith('%') and not line.endswith('?')])
        )
        self.statement_cache[cell_counter()] = compute_lineno_to_stmt_mapping(self._last_cell_ast)
        if self._last_refused_code is None or cell != self._last_refused_code:
            for name in precheck(self._last_cell_ast, self.global_scope.data_cell_by_name.keys()):
                if isinstance(name, str):
                    nodes = [self.global_scope.data_cell_by_name.get(name, None)]
                elif isinstance(name, AttributeSymbolChain):
                    nodes = self.global_scope.gen_data_cells_for_attr_symbol_chain(name, self.namespaces)
                else:
                    logger.warning('invalid type for name %s', name)
                    continue
                for node in nodes:
                    if node is None:
                        continue
                    if node.is_stale() and self._disable_level < 2:
                        _safety_warning(name, node.defined_cell_num, node.required_cell_num, node.fresher_ancestors)
                        self.stale_dependency_detected = True
                        self._last_refused_code = cell
                        if self._disable_level == 0:
                            return True
        else:
            # TODO: break dependency chain here
            pass

        self._last_refused_code = None
        return False

    def _make_cell_magic(self, cell_magic_name):
        def _dependency_safety(_, cell: str):
            if self._disable_level == 3:
                run_cell(cell)
                return

            with save_number_of_currently_executing_cell():
                # Stage 1: Precheck.
                if self._precheck_for_stale(cell):
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
            # TODO: add more explicit way to check for an error in dependency tracing code
            if self.trace_state.prev_trace_stmt_in_cur_frame is None:
                if untraced_backup is not None:
                    untraced_backup()
            else:
                self._reset_trace_state_hook()

    def _reset_trace_state_hook(self):
        if self.dependency_tracking_enabled:
            self.trace_state.prev_trace_stmt_in_cur_frame.finished_execution_hook()
        self.attr_trace_manager.reset()
        if self._save_prev_trace_state_for_tests:
            self.prev_trace_state = self.trace_state
        self.trace_state = TraceState(self)

    def _make_line_magic(self, line_magic_name):
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

        if line_magic_name is not None:
            _safety.__name__ = line_magic_name
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
