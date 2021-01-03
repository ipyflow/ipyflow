import ast
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Tuple
    from nbsafety.safety import NotebookSafety


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class ChainedNodeTransformer(ast.NodeTransformer):
    def __init__(
            self,
            safety: 'NotebookSafety',
            transformers: 'Tuple[Callable[[ast.AST, Tuple[Any, ...]], Tuple[ast.AST, Tuple[Any, ...]]], ...]'
    ):
        self.safety = safety
        self.chained = transformers

    def visit(self, node: 'ast.AST'):
        prev_outputs: 'Tuple[Any, ...]' = ()
        for step, transformer in enumerate(self.chained):
            try:
                node, prev_outputs = transformer(node, *prev_outputs)
            except Exception as e:
                self.safety.set_ast_transformer_raised(e)
                logger.warning("exception during ast rewriting step %d: %s" % (step + 1, e))
                raise e
        return node
