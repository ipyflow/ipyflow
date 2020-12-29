import ast


class ChainedNodeTransformer(ast.NodeTransformer):
    def __init__(self, *transformers):
        if len(transformers) == 1 and hasattr(transformers[0], '__iter__'):
            self.chained = list(transformers[0])
        else:
            self.chained = transformers

    def visit(self, node: 'ast.AST'):
        for transformer in self.chained:
            node = transformer.visit(node)
        return node
