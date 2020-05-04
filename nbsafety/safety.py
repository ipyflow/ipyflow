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

        self._disable_level = 0

    def _make_cell_magic(self, cell_magic_name):
        def _dependency_safety(_, cell: str):
            with save_number_of_currently_executing_cell():
                if self._disable_level == 3:
                    run_cell(cell)
                    return

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
                        if node.is_stale() and self._disable_level < 2:
                            _safety_warning(name, node.defined_cell_num, node.required_cell_num, node.fresher_ancestors)
                            self.stale_dependency_detected = True
                            self._last_refused_code = cell
                            if self._disable_level == 0:
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
            if not line or line[0] not in [
                "show_graph", "show_dependency", "show_stale", "set_disable_level",
                "remove_dependency", "add_dependency", "turn_off_warnings_for", "turn_on_warnings_for"
            ]:
                print("""Options:

show_graph: 
    -This will print out the dependency graph of global variables. Stale nodes are labeled red. Notice that user might need to call this twice to have it to work.

show_dependency <variable_name> <variable_name2> ...: 
    -This will print out the dependencies for given global variables. Multiple variables should be separated with spaces.

show_stale: 
    -This will print out all the global variables that are stale. 

set_disable_level <integer>:
    -level 0: Warning,    Stop Code,   Record new dependencies, (Full functionality)
    -level 1: Warning,    Run code,    Record new dependencies, (Don't stop at the warning)
    -level 2: No Warning, Run code,    Record new dependencies, (Don't show warnings)
    -level 3: No warning, Run cocde,   No new dependencies,     (Original Kernel)

remove_dependency <parent_name> <child_name>:
    -This will remove the dependency between parent variable and the child variable.

add_dependency <parent_name> <child_name>:
    -This will add the dependency between parent variable and the child variable.

turn_off_warnings_for  <variable_name> <variable_name2> ...: 
    -This will turn off the warnings for given global variables. These variables will never be considered as stale anymore. Multiple variables should be seperated with spaces.


turn_off_warnings_for  <variable_name> <variable_name2> ...: 
    -This will turn the warnings back on for given global variables. These variables could have a stale dependency now. Multiple variables should be seperated with spaces.""")
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
                    node_color=["#ff0000" if self.global_scope.lookup_data_cell_by_name(name).is_stale() else "#cccccc" for name in graph.nodes()],
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
                    print("The only DataCell with stale dependencies is:", str(stale_set.pop()))
                else:
                    print("DataCells with stale dependencies are:", [str(n) for n in stale_set])
            elif line[0] == "set_disable_level":
                if len(line) != 2 or line[1] not in ['0', '1', '2', '3']:
                    print("""Usage: %safety set_disable_level <integer>
-level 0: Warning,    Stop Code,   Record new dependencies, (Full functionality)
-level 1: Warning,    Run code,    Record new dependencies, (Don't stop at the warning)
-level 2: No Warning, Run code,    Record new dependencies, (Don't show warnings)
-level 3: No warning, Run cocde,   No new dependencies,     (Original Kernel)""")
                    return
                self._disable_level = int(line[1])
            elif line[0] == "remove_dependency":
                if len(line) != 3:
                    print("Usage: %safety remove_dependency <parent_name> <child_name>")
                    return
                parent_data_cell = self.global_scope.lookup_data_cell_by_name(line[1])
                if not parent_data_cell:
                    print("Cannot find DataCell", line[1])
                    return
                child_data_cell = self.global_scope.lookup_data_cell_by_name(line[2])
                if not child_data_cell:
                    print("Cannot find DataCell", line[2])
                    return
                if child_data_cell not in parent_data_cell.children or parent_data_cell not in child_data_cell.parents:
                    print("Two DataCells do not have a dependency relation")
                    return
                parent_data_cell.children.remove(child_data_cell)
                child_data_cell.parents.remove(parent_data_cell)
            elif line[0] == "add_dependency":
                if len(line) != 3:
                    print("Usage: %safety add_dependency <parent_name> <child_name>")
                    return
                parent_data_cell = self.global_scope.lookup_data_cell_by_name(line[1])
                if not parent_data_cell:
                    print("Cannot find DataCell", line[1])
                    return
                child_data_cell = self.global_scope.lookup_data_cell_by_name(line[2])
                if not child_data_cell:
                    print("Cannot find DataCell", line[2])
                    return
                if child_data_cell in parent_data_cell.children and parent_data_cell in child_data_cell.parents:
                    print("Two DataCells already have a dependency relation")
                    return
                parent_data_cell.children.add(child_data_cell)
                child_data_cell.parents.add(parent_data_cell)
            elif line[0] == "turn_off_warnings_for":
                if len(line) == 1:
                    print("Usage: %safety turn_off_warnings_for <variable_name> <variable_name2> ...")
                    return
                for data_cell_name in line[1:]:
                    data_cell = self.global_scope.lookup_data_cell_by_name(data_cell_name)
                    if data_cell:
                        data_cell.no_warning = True
                        print("Warnings are turned off for", data_cell_name)
                    else:
                        print("Cannot find DataCell", data_cell_name)
            elif line[0] == "turn_on_warnings_for":
                if len(line) == 1:
                    print("Usage: %safety turn_on_warnings_for <variable_name> <variable_name2> ...")
                    return
                for data_cell_name in line[1:]:
                    data_cell = self.global_scope.lookup_data_cell_by_name(data_cell_name)
                    if data_cell:
                        data_cell.no_warning = False
                        print("Warnings are turned on for", data_cell_name)
                    else:
                        print("Cannot find DataCell", data_cell_name)

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
