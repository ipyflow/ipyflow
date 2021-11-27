# -*- coding: future_annotations -*-
import ast
import logging
import traceback
from collections import defaultdict
from typing import TYPE_CHECKING, cast

from nbsafety.singletons import nbs, tracer
from nbsafety.data_model.code_cell import cells
from nbsafety.tracing.ast_eavesdrop import AstEavesdropper
from nbsafety.tracing.stmt_inserter import StatementInserter
from nbsafety.tracing.stmt_mapper import StatementMapper

if TYPE_CHECKING:
    from typing import Dict, Optional, Set, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class AstRewriter(ast.NodeTransformer):
    def __init__(self, module_id: Optional[int] = None):
        self._module_id: Optional[int] = module_id
        self._augmented_positions_by_type: Dict[str, Set[Tuple[int, int]]] = defaultdict(set)

    def register_augmented_position(self, mod_type: str, lineno: int, col_offset: int) -> None:
        self._augmented_positions_by_type[mod_type].add((lineno, col_offset))

    def visit(self, node: ast.AST):
        assert isinstance(node, ast.Module)
        mapper = StatementMapper(
            tracer().statement_cache[id(node) if self._module_id is None else self._module_id],
            self._augmented_positions_by_type,
        )
        orig_to_copy_mapping = mapper(node)
        cells().current_cell().to_ast(override=cast(ast.Module, orig_to_copy_mapping[id(node)]))
        # very important that the eavesdropper does not create new ast nodes for ast.stmt (but just
        # modifies existing ones), since StatementInserter relies on being able to map these
        events_with_handlers = tracer().events_with_registered_handlers
        node = AstEavesdropper(orig_to_copy_mapping, events_with_handlers).visit(node)
        node = StatementInserter(orig_to_copy_mapping, events_with_handlers, tracer().loop_guards).visit(node)
        return node


class SafetyAstRewriter(AstRewriter):
    def __init__(self, module_id: Optional[int] = None):
        super().__init__(module_id)

    def visit(self, node: ast.AST):
        try:
            return super().visit(node)
        except Exception as e:
            nbs().set_exception_raised_during_execution(e)
            traceback.print_exc()
            raise e
