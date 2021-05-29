# -*- coding: future_annotations -*-
import ast
import logging
import traceback
from typing import TYPE_CHECKING, cast

from nbsafety.singletons import nbs
from nbsafety.tracing.ast_eavesdrop import AstEavesdropper
from nbsafety.tracing.stmt_inserter import StatementInserter
from nbsafety.tracing.stmt_mapper import StatementMapper

if TYPE_CHECKING:
    from typing import Optional
    from nbsafety.types import CellId


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class SafetyAstRewriter(ast.NodeTransformer):
    def __init__(self, cell_id: Optional[CellId]):
        self._cell_id: Optional[CellId] = cell_id

    def visit(self, node: ast.AST):
        assert isinstance(node, ast.Module)
        try:
            mapper = StatementMapper(
                self._cell_id,
                nbs().statement_cache[nbs().cell_counter()],
                nbs().ast_node_by_id,
                nbs().cell_id_by_ast_id,
                nbs().parent_node_by_id,
            )
            orig_to_copy_mapping = mapper(node)
            nbs().cell_ast_by_counter[nbs().cell_counter()] = cast(ast.Module, orig_to_copy_mapping[id(node)])
            # very important that the eavesdropper does not create new ast nodes for ast.stmt (but just
            # modifies existing ones), since StatementInserter relies on being able to map these
            node = AstEavesdropper(orig_to_copy_mapping).visit(node)
            node = StatementInserter(self._cell_id, orig_to_copy_mapping).visit(node)
        except Exception as e:
            nbs().set_exception_raised_during_execution(e)
            traceback.print_exc()
            raise e
        return node
