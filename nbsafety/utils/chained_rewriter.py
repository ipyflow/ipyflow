import ast
from nbsafety.utils.mixins import SkipNodesMixin


class ChainedNodeTransformer(ast.NodeTransformer):
    def __init__(self, *transformers):
        if len(transformers) == 1 and hasattr(transformers[0], '__iter__'):
            self.chained = list(transformers[0])
        else:
            self.chained = transformers

    def visit(self, node: 'ast.AST'):
        prev = None
        for transformer in self.chained:
            if prev is not None and isinstance(transformer, SkipNodesMixin):
                transformer.skip_nodes = getattr(prev, 'skip_nodes', set())
            node = transformer.visit(node)
            prev = transformer
        return node
