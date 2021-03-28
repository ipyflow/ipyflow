# -*- coding: future_annotations -*-
import ast
from contextlib import contextmanager
from typing import Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Union


class SkipUnboundArgsMixin(ast.NodeVisitor):
    # Only need to check for default arguments
    def visit_arguments(self, node):
        self.visit(node.defaults)
        self.visit(node.kw_defaults)


class VisitListsMixin(ast.NodeVisitor):
    def generic_visit(self, node: Union[ast.AST, Sequence[ast.AST]]):
        if node is None:
            return
        elif isinstance(node, Sequence):
            for item in node:
                self.visit(item)
        else:
            super().generic_visit(node)


class SaveOffAttributesMixin:
    @contextmanager
    def push_attributes(self, **kwargs):
        for k in kwargs:
            if not hasattr(self, k):
                raise AttributeError('requested to save unfound attribute %s of object %s' % (k, self))
        saved_attributes = {}
        for k in kwargs:
            saved_attributes[k] = getattr(self, k)
        for k, v in kwargs.items():
            setattr(self, k, v)
        yield
        for k, v in saved_attributes.items():
            setattr(self, k, v)
