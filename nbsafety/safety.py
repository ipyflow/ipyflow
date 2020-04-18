import ast
import logging
import sys

from IPython import get_ipython
from IPython.core.magic import register_cell_magic

from .precheck import PreCheck
from .scope import Scope
from .updates import UpdateDependency


def _safety_warning(name, defined_CN, pair):
    logging.warning(
        "{} defined in cell {} may have a stale dependency on {} (last updated in cell {}).".format(
            name, defined_CN, pair[1].name, pair[0]
        )
    )


class DependencySafety(object):
    """Holds all the state necessary to detect stale dependencies in Jupyter notebooks."""
    def __init__(self, cell_magic_name=None):
        self.counter = [0]
        self.global_scope = Scope(self.counter, 'global')
        self.func_id_to_scope_object = {}
        self.frame_dict_by_scope = {}
        self.stale_dependency_detected = False
        self._cell_magic = self._make_cell_magic(cell_magic_name)

    def _capture_frame_at_run_time(self, frame, event, _):
        original_frame = frame
        if 'ipython-input' in frame.f_code.co_filename:
            if event == 'call':
                path = ()
                while frame.f_code.co_name != '<module>':
                    path = (frame.f_code.co_name,) + path
                    frame = frame.f_back
                if path not in self.frame_dict_by_scope:
                    self.frame_dict_by_scope[path] = original_frame

    def _make_cell_magic(self, cell_magic_name):
        def _dependency_safety(_, cell: str):
            # We increase the counter by one each time this cell magic function is called
            self.counter[0] += 1

            # We get the ast.Module node by parsing the cell
            ast_tree = ast.parse(cell)

            # State 1: Precheck.
            # Precheck process. First obtain the names that need to be checked. Then we check if their
            # defined_CN is greater than or equal to required, if not we give a warning and return.
            for name in PreCheck().precheck(ast_tree, self.global_scope):
                node = self.global_scope.get_node_by_name_current_scope(name)
                if node.defined_CN < node.required_CN_node_pair[0]:
                    _safety_warning(name, node.defined_CN, node.required_CN_node_pair)
                    self.stale_dependency_detected = True
                    return

            # Stage 2: Trace / run the cell.
            sys.settrace(lambda *args: self.__class__._capture_frame_at_run_time(self, *args))
            get_ipython().run_cell(cell)
            sys.settrace(None)

            # Stage 3: Update dependencies.
            UpdateDependency(self).updateDependency(ast_tree, self.global_scope)
            return
        if cell_magic_name is not None:
            # TODO (smacke): probably not a great idea to rely on this
            _dependency_safety.__name__ = cell_magic_name
        return register_cell_magic(_dependency_safety)

    @property
    def cell_magic_name(self):
        return self._cell_magic.__name__

    def test_and_clear_detected_flag(self):
        ret = self.stale_dependency_detected
        self.stale_dependency_detected = False
        return ret
