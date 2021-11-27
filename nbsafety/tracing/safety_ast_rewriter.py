# -*- coding: future_annotations -*-
import ast
import logging
import traceback
from typing import TYPE_CHECKING, cast

from nbsafety.analysis.reactive_modifiers import AugmentedAtom
from nbsafety.singletons import nbs, tracer
from nbsafety.data_model.code_cell import cells
from nbsafety.tracing.ast_eavesdrop import AstEavesdropper
from nbsafety.tracing.stmt_inserter import StatementInserter
from nbsafety.tracing.stmt_mapper import StatementMapper

if TYPE_CHECKING:
    from typing import Dict, Set, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class SafetyAstRewriter(ast.NodeTransformer):
    def __init__(self):
        self._reacive_var_positions: Set[Tuple[int, int]] = set()
        self._blocking_var_positions: Set[Tuple[int, int]] = set()

    def register_reactive_var_position(self, sym_type: AugmentedAtom, lineno: int, col_offset: int) -> None:
        if sym_type == AugmentedAtom.reactive:
            self._reacive_var_positions.add((lineno, col_offset))
        elif sym_type == AugmentedAtom.blocking:
            self._blocking_var_positions.add((lineno, col_offset))
        else:
            raise ValueError('augmented symbol prefixed with "%s" not handled yet' % sym_type.marker)

    def visit(self, node: ast.AST):
        assert isinstance(node, ast.Module)
        try:
            mapper = StatementMapper(
                tracer().statement_cache[nbs().cell_counter()],
                self._reacive_var_positions,
                self._blocking_var_positions,
            )
            orig_to_copy_mapping = mapper(node)
            cells().current_cell().to_ast(override=cast(ast.Module, orig_to_copy_mapping[id(node)]))
            # very important that the eavesdropper does not create new ast nodes for ast.stmt (but just
            # modifies existing ones), since StatementInserter relies on being able to map these
            events_with_handlers = frozenset(tracer().EVENT_HANDLERS_BY_CLASS[tracer().__class__].keys())
            node = AstEavesdropper(orig_to_copy_mapping, events_with_handlers).visit(node)
            node = StatementInserter(orig_to_copy_mapping, events_with_handlers).visit(node)
        except Exception as e:
            nbs().set_exception_raised_during_execution(e)
            traceback.print_exc()
            raise e
        return node
