import ast
import logging


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class ChainedNodeTransformer(ast.NodeTransformer):
    def __init__(self, *transformers):
        if len(transformers) == 1 and hasattr(transformers[0], '__iter__'):
            self.chained = list(transformers[0])
        else:
            self.chained = transformers

    def visit(self, node: 'ast.AST'):
        prev_outputs = ()
        for step, transformer in enumerate(self.chained):
            try:
                node, prev_outputs = transformer(node, *prev_outputs)
            except Exception as e:
                logger.warning("exception during ast rewriting step %d: %s" % (step + 1, e))
                raise e
        return node
