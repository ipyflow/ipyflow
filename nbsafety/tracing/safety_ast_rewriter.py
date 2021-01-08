import ast
import logging
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
        step = '<init>'
        try:
            mapper = StatementMapper(self.safety.statement_cache[self.safety.cell_counter()], self.safety.stmt_by_id)
            step, (node, orig_to_copy_mapping) = '<create ast mappings>', mapper(node)
            eavesdropper = AstEavesdropper()
            inserter = StatementInserter(eavesdropper, orig_to_copy_mapping)
            step, (node, skip_nodes) = '<insert stmts>', inserter(node)
            eavesdropper.skip_nodes = skip_nodes
            step, node = '<ast eavesdrop>', eavesdropper.visit(node)
        except Exception as e:
            self.safety.set_ast_transformer_raised(e)
            logger.warning("exception during ast rewriting step %s: %s" % (step, e))
            raise e
        return node
