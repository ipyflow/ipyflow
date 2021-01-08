import ast
import logging
import traceback
from typing import TYPE_CHECKING

from nbsafety.tracing.ast_eavesdrop import AstEavesdropper
from nbsafety.tracing.stmt_inserter import StatementInserter
from nbsafety.tracing.stmt_mapper import StatementMapper

if TYPE_CHECKING:
    from nbsafety.safety import NotebookSafety


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class SafetyAstRewriter(ast.NodeTransformer):
    def __init__(self, safety: 'NotebookSafety'):
        self.safety = safety

    def visit(self, node: 'ast.AST'):
        try:
            mapper = StatementMapper(self.safety.statement_cache[self.safety.cell_counter()], self.safety.stmt_by_id)
            node, orig_to_copy_mapping = mapper(node)
            eavesdropper = AstEavesdropper()
            inserter = StatementInserter(eavesdropper, orig_to_copy_mapping)
            node, skip_nodes = inserter(node)
            eavesdropper.skip_nodes |= skip_nodes
            node = eavesdropper.visit(node)
        except Exception as e:
            self.safety.set_ast_transformer_raised(e)
            traceback.print_exc()
            raise e
        return node
