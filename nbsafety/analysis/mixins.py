# -*- coding: utf-8 -*-
from __future__ import annotations
import ast
from contextlib import contextmanager


class SkipUnboundArgsMixin(object):
    # Only need to check for default arguments
    def visit_arguments(self: ast.NodeVisitor, node):
        self.visit(node.defaults)
        self.visit(node.kw_defaults)


class VisitListsMixin(object):
    def generic_visit(self: ast.NodeVisitor, node):
        if node is None:
            return
        elif isinstance(node, list):
            for item in node:
                self.visit(item)
        else:
            ast.NodeVisitor.generic_visit(self, node)


class SaveOffAttributesMixin(object):
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
