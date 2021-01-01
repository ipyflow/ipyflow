import ast


class ChainedNodeTransformer(ast.NodeTransformer):
    def __init__(self, *transformers):
        if len(transformers) == 1 and hasattr(transformers[0], '__iter__'):
            self.chained = list(transformers[0])
        else:
            self.chained = transformers

    def visit(self, node: 'ast.AST'):
        prev_outputs = ()
        for transformer in self.chained:
            node, prev_outputs = transformer(node, *prev_outputs)
        return node
