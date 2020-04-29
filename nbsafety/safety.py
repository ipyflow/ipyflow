import ast
import logging
import sys
from types import FrameType
from typing import Any, Dict, Set, Tuple

from IPython import get_ipython
from IPython.core.magic import register_cell_magic, register_line_magic
import networkx as nx

from .analysis.precheck import precheck
from .analysis.updates import UpdateDependency
from .data_cell import DataCell, FunctionDataCell
from .scope import Scope
from .tracing.tracer import make_tracer
from .tracing.trace_state import TraceState


def _safety_warning(name: str, defined_cell_num: int, required_cell_num: int, fresher_ancestors: Set[DataCell]):
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
        self.frame_dict_by_scope: Dict[Tuple[str, ...], FrameType] = {}
        self.data_cell_by_ref: Dict[int, DataCell] = {}
        self.global_data_cell_by_name: Dict[str, DataCell] = {}
        self.stale_dependency_detected = False
        self.trace_state = TraceState(self.global_scope)
        self._cell_magic = self._make_cell_magic(cell_magic_name)
        # Maybe switch update this too when you are implementing the usage of cell_magic_name?
        self._line_magic = self._make_line_magic(line_magic_name)

    def _capture_frame_at_run_time(self, frame: FrameType, event: str, _):
        original_frame = frame
        if 'ipython-input' in frame.f_code.co_filename:
            if event == 'call':
                path: Tuple[str, ...] = ()
                while frame.f_code.co_name != '<module>':
                    path = (frame.f_code.co_name,) + path
                    frame = frame.f_back
                # TODO(smacke): problem here
                # Frame is stored by call path, but accessed by scope path.
                # These are not necessarily the same!
                if path not in self.frame_dict_by_scope:
                    self.frame_dict_by_scope[path] = original_frame

    def make_data_cell_for_obj(
            self,
            name: str,
            obj: Any,
            deps: Set[DataCell],
            scope: str,
            add=False,
            is_function_def=False
    ):
        if scope == 'global' and name in self.global_data_cell_by_name:
            dc = self.global_data_cell_by_name[name]
            dc.update_deps(deps, add=add)
            # TODO: garbage collect old id
            self.data_cell_by_ref[id(obj)] = dc
            return
        if is_function_def:
            dc = FunctionDataCell(self.trace_state.cur_frame_scope.make_child_scope(name), name, scope, deps)
        else:
            dc = DataCell(name, scope, deps)
        # TODO: need more disambiguation than 'id'
        self.data_cell_by_ref[id(obj)] = dc
        if scope == 'global':
            self.global_data_cell_by_name[name] = dc
        for dep in deps:
            dep.children.add(dc)

    def _make_cell_magic(self, cell_magic_name):
        def _dependency_safety_old(_, cell: str):
            # We get the ast.Module node by parsing the cell
            ast_tree = ast.parse(cell)

            # State 1: Precheck.
            # Precheck process. First obtain the names that need to be checked. Then we check if their
            # defined_cell_num is greater than or equal to required, if not we give a warning and return.
            for name in precheck(ast_tree, self.global_scope.variable_dict.keys()):
                node = self.global_scope.get_node_by_name_current_scope(name)
                if node.defined_cell_num < node.required_cell_num:
                    _safety_warning(name, node.defined_cell_num, node.required_cell_num, node.fresher_ancestors)
                    self.stale_dependency_detected = True
                    return

            # Stage 2: Trace / run the cell.
            sys.settrace(lambda *args: self.__class__._capture_frame_at_run_time(self, *args))
            get_ipython().run_cell(cell, store_history=True)
            sys.settrace(None)

            # Stage 3: Update dependencies.
            self.global_scope.frame_dict = self.frame_dict_by_scope[()].f_locals
            UpdateDependency(self)(ast_tree)
            return

        def _dependency_safety(_, cell: str):
            # We get the ast.Module node by parsing the cell
            ast_tree = ast.parse(cell)

            # State 1: Precheck.
            # Precheck process. First obtain the names that need to be checked. Then we check if their
            # defined_cell_num is greater than or equal to required, if not we give a warning and return.
            for name in precheck(ast_tree, self.global_data_cell_by_name.keys()):
                node = self.global_data_cell_by_name[name]
                if node.defined_cell_num < node.required_cell_num:
                    _safety_warning(name, node.defined_cell_num, node.required_cell_num, node.fresher_ancestors)
                    self.stale_dependency_detected = True
                    return

            # Stage 2: Trace / run the cell, updating dependencies as they are encountered.
            sys.settrace(make_tracer(self, self.trace_state))
            # Test code doesn't run the full kernel and should therefore set store_history=True
            # (e.g. in order to increment the cell numbers)
            get_ipython().run_cell(cell, store_history=True)
            sys.settrace(None)
            self.trace_state.cur_frame_last_line.make_lhs_data_cells_if_has_lval()
            # TODO: use context manager to handle this automatically
            self.trace_state = TraceState(self.global_scope)  # reset the trace state
            return

        if cell_magic_name is not None:
            # TODO (smacke): probably not a great idea to rely on this
            _dependency_safety.__name__ = cell_magic_name
        return register_cell_magic(_dependency_safety)

    def _make_line_magic(self, line_magic_name):
        def _safety(line: str):
            if line == "show_graph":
                graph = nx.DiGraph()
                for name in self.global_scope.variable_dict:
                    graph.add_node(name)
                for node in self.global_scope.variable_dict.values():
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
