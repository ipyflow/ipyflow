import ast
import logging
import sys
from typing import TYPE_CHECKING

from IPython.core.magic import register_cell_magic, register_line_magic
import networkx as nx

from .analysis import precheck, compute_lineno_to_stmt_mapping
from .ipython_utils import cell_counter, run_cell, save_number_of_currently_executing_cell
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
        self.namespaces: Dict[int, Scope] = {}
        self.statement_cache: Dict[int, Dict[int, ast.stmt]] = {}
        self.stale_dependency_detected = False
        self.trace_state = TraceState(self)
        self._cell_magic = self._make_cell_magic(cell_magic_name)
        # Maybe switch update this too when you are implementing the usage of cell_magic_name?
        self._line_magic = self._make_line_magic(line_magic_name)
        self._last_refused_code: Optional[str] = None
        self._last_cell_ast: Optional[ast.Module] = None
        self._track_dependencies = True

    def _make_cell_magic(self, cell_magic_name):
        def _dependency_safety(_, cell: str):
            with save_number_of_currently_executing_cell():

                # Stage 1: Precheck.
                # Precheck process. First obtain the names that need to be checked. Then we check if their
                # defined_cell_num is greater than or equal to required, if not we give a warning and return.
                self._last_cell_ast = ast.parse('\n'.join(
                    [line for line in cell.strip().split('\n') if not line.startswith('%')])
                )
                self.statement_cache[cell_counter()] = compute_lineno_to_stmt_mapping(self._last_cell_ast)
                if self._last_refused_code is None or cell != self._last_refused_code:
                    for name in precheck(self._last_cell_ast, self.global_scope.data_cell_by_name.keys()):
                        node = self.global_scope.data_cell_by_name[name]
                        if node.is_stale():
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
                # Test code doesn't run the full kernel and should therefore set store_history=True
                # (e.g. in order to increment the cell numbers)
                run_cell(cell)
                sys.settrace(None)
                self._reset_trace_state_hook()
                return

        if cell_magic_name is not None:
            # TODO (smacke): probably not a great idea to rely on this
            _dependency_safety.__name__ = cell_magic_name
        return register_cell_magic(_dependency_safety)

    def _reset_trace_state_hook(self):
        if self.trace_state.cur_frame_last_stmt is None:
            logging.warning('last executed statement not available after cell done executing; this should not happen')
        elif self.dependency_tracking_enabled:
            self.trace_state.cur_frame_last_stmt.make_lhs_data_cells_if_has_lval()
        self.trace_state = TraceState(self)

    def _make_line_magic(self, line_magic_name):
        def _safety(line: str):
            line = line.split()
            if not line:
                return
            if line[0] == "show_graph":
                graph = nx.DiGraph()
                for name in self.global_scope.data_cell_by_name:
                    graph.add_node(name)
                for node in self.global_scope.data_cell_by_name.values():
                    name = node.name
                    for child_node in node.children:
                        graph.add_edge(name, child_node.name)
                nx.draw_networkx(
                    graph,
                    node_color=["#ff0000" if self.global_scope.data_cell_by_name[name].is_stale() else "#cccccc" for name in graph.nodes()],
                    arrowstyle='->',
                    arrowsize=30,
                    node_size=1000,
                    pos=nx.drawing.layout.planar_layout(graph)
                )
            elif line[0] == "show_dependency":
                if len(line) == 1:
                    print("Usage: %safety show_dependency <variable_name> <variable_name2> ...")
                    return
                for data_cell_name in line[1:]:
                    data_cell = self.global_scope.lookup_data_cell_by_name(data_cell_name)
                    if data_cell:
                        print("DataCell {} is dependent on {}".format(data_cell_name, [str(n) for n in data_cell.parents] if data_cell.parents else "Nothing"))
                    else:
                        print("Cannot find DataCell", data_cell_name)
            elif line[0] == "show_stale":
                stale_set = set()
                for data_cell in self.global_scope.data_cell_by_name.values():
                    if data_cell.is_stale():
                        stale_set.add(data_cell)
                if not stale_set:
                    print("No DataCell has stale dependency for now!")
                elif len(stale_set) == 1:
                    print("The only DataCell with stale depedencies is:", str(stale_set.pop()))
                else:
                    print("DataCells with stale depedencies are:", [str(n) for n in stale_set])
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
