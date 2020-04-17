import ast
import logging
import sys

from IPython.core.magic import register_cell_magic

from .precheck import PreCheck
from .scope import Scope
from .tracing import capture_frame_at_run_time
from .updates import UpdateDependency


@register_cell_magic
def dependency_safety(line: str, cell: str):
    # We increase the counter by one each time this cell magic function is called
    dependency_safety.counter += 1

    # We get the ast.Module node by parsing the cell
    ast_tree = ast.parse(cell)

    # State 1: Precheck.
    # Precheck process. First obtain the names that need to be checked. Then we check if their
    # defined_CN is greater than or equal to required, if not we give a warning and return.
    for name in PreCheck().precheck(ast_tree, dependency_safety.global_scope):
        node = dependency_safety.global_scope.get_node_by_name_current_scope(name)
        if node.defined_CN < node.required_CN_node_pair[0]:
            dependency_safety.warning(name, node.defined_CN, node.required_CN_node_pair)
            return

    # Stage 2: Trace / run the cell.
    sys.settrace(capture_frame_at_run_time)
    get_ipython().run_cell(cell)
    sys.settrace(None)

    # Stage 3: Update dependencies.
    UpdateDependency(dependency_safety).updateDependency(ast_tree, dependency_safety.global_scope)
    return


# Make sure to run this init function before using the magic cell
def dependency_safety_init():
    dependency_safety.counter = 1
    dependency_safety.global_scope = Scope(dependency_safety, "global")

    def _safety_warning(name, defined_CN, pair):
        logging.warning(
            "{} was defined in cell {}, but its ancestor dependency node {} was redefined in cell {}.".format(
                name, defined_CN, pair[1].name, pair[0]
            )
        )

    dependency_safety.warning = _safety_warning
    dependency_safety.func_id_to_scope_object = {}
    capture_frame_at_run_time.dictionary = {}

dependency_safety_init()
