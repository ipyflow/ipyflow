# -*- coding: utf-8 -*-
import ast


class CommonEqualityMixin(object):
    def __eq__(self, other):
        return isinstance(other, self.__class__) and (self.__dict__ == other.__dict__)

    def __ne__(self, other):
        return not (self == other)


class SkipNodesMixin(ast.NodeTransformer):
    def visit(self, node: 'ast.AST') -> 'ast.AST':
        if id(node) in getattr(self, 'skip_nodes', []):
            return node
        return super().visit(node)
