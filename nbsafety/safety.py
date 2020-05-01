import logging
import sys
from typing import TYPE_CHECKING

from IPython import get_ipython
from IPython.core.magic import register_cell_magic, register_line_magic
import networkx as nx

from .analysis import precheck
from .ipython_utils import save_number_of_currently_executing_cell
from .scope import Scope
from .tracing import make_tracer, TraceState

if TYPE_CHECKING:
    from typing import Dict, Set, Optional
    from .data_cell import DataCell


def _safety_warning(name: str, defined_cell_num: int, required_cell_num: int, fresher_ancestors: 'Set[DataCell]'):
    logging.warning(
        f'{name} defined in cell {defined_cell_num} may depend on '
        f'old version(s) of [{", ".join(str(dep) for dep in fresher_ancestors)}] '
        f'(lastest update in cell {required_cell_num}).'
    )


class DependencySafety(object):
    """Holds all the state necessary to detect stale dependencies in Jupyter notebooks."""
    def __init__(self, cell_magic_name=None, line_magic_name=None):
        self.global_scope = Scope()
        self.func_id_to_scope_object: Dict[int, Scope] = {}
        self.data_cell_by_ref: Dict[int, DataCell] = {}
        self.stale_dependency_detected = False
        self.trace_state = TraceState(self.global_scope)
        self._cell_magic = self._make_cell_magic(cell_magic_name)
        # Maybe switch update this too when you are implementing the usage of cell_magic_name?
        self._line_magic = self._make_line_magic(line_magic_name)
        self._last_refused_code: Optional[str] = None

    def _make_cell_magic(self, cell_magic_name):
        def _dependency_safety(_, cell: str):
            # State 1: Precheck.
            # Precheck process. First obtain the names that need to be checked. Then we check if their
            # defined_cell_num is greater than or equal to required, if not we give a warning and return.
            if self._last_refused_code is None or cell != self._last_refused_code:
                for name in precheck(cell, self.global_scope.data_cell_by_name.keys()):
                    node = self.global_scope.data_cell_by_name[name]
                    if node.defined_cell_num < node.required_cell_num:
                        _safety_warning(name, node.defined_cell_num, node.required_cell_num, node.fresher_ancestors)
                        self.stale_dependency_detected = True
                        self._last_refused_code = cell
                        return
            else:
                # TODO: break dependency chain here
                pass

            self._last_refused_code = None

            # TODO: use context manager to handle these next lines automatically
            # Stage 2: Trace / run the cell, updating dependencies as they are encountered.
            sys.settrace(make_tracer(self))
            with save_number_of_currently_executing_cell():
                # Test code doesn't run the full kernel and should therefore set store_history=True
                # (e.g. in order to increment the cell numbers)
                get_ipython().run_cell(cell, store_history=True)
                sys.settrace(None)
                self._reset_trace_state_hook()
            return

        if cell_magic_name is not None:
            # TODO (smacke): probably not a great idea to rely on this
            _dependency_safety.__name__ = cell_magic_name
        return register_cell_magic(_dependency_safety)

    def _reset_trace_state_hook(self):
        if self.trace_state.cur_frame_last_line is None:
            logging.warning('last executed line not available after cell done executing; this should not happen')
        else:
            self.trace_state.cur_frame_last_line.make_lhs_data_cells_if_has_lval()
        self.trace_state = TraceState(self.global_scope)

    def _make_line_magic(self, line_magic_name):
        def _safety(line: str):
            if line == "show_graph":
                graph = nx.DiGraph()
                for name in self.global_scope.data_cell_by_name:
                    graph.add_node(name)
                for node in self.global_scope.data_cell_by_name.values():
                    name = node.name
                    for child_node in node.children:
                        graph.add_edge(name, child_node.name)
                nx.draw_networkx(
                    graph,
                    node_color="#cccccc",
                    arrowstyle='->',
                    arrowsize=30,
                    node_size=1000,
                    pos=nx.drawing.layout.planar_layout(graph)
                )
        if line_magic_name is not None:
            _safety.__name__ = line_magic_name
        return register_line_magic(_safety)

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
