# -*- coding: utf-8 -*-
from __future__ import annotations
import ast

from ..analysis.hyperedge import get_hyperedge_lvals_and_rvals


def lookup_obj_by_name(name, frame, call_depth):
    if call_depth <= 1:
        scope = 'global'
    else:
        scope = 'local'
    if name in frame.f_locals:
        return frame.f_locals[name], scope
    return frame.f_globals[name], 'global'


# TODO: maybe frame, scope, indentation, etc
class CodeLine(object):
    def __init__(self, safety, text, ast_node, lineno, call_depth, frame):
        self.safety = safety
        self.text = text
        self.ast_node = ast_node
        self.lineno = lineno
        self.call_depth = call_depth
        self.frame = frame
        self.extra_dependencies = set()

    def lookup_obj_by_name(self, name):
        if self.call_depth <= 1:
            scope = 'global'
        else:
            scope = 'local'
        if name in self.frame.f_locals:
            return self.frame.f_locals[name], scope
        return self.frame.f_globals[name], 'global'

    def compute_rval_dependencies(self, rval_names=None):
        if rval_names is None:
            _, rval_names = get_hyperedge_lvals_and_rvals(self.ast_node)
        rval_data_cells = set()
        for name in rval_names:
            try:
                obj, _ = self.lookup_obj_by_name(name)
            except KeyError:
                continue
            rval_data_cells.add(self.safety.data_cell_by_ref[id(obj)])
        return rval_data_cells | self.extra_dependencies

    def make_lhs_data_cells(self):
        lval_names, rval_names = get_hyperedge_lvals_and_rvals(self.ast_node)
        rval_deps = self.compute_rval_dependencies(rval_names=rval_names-lval_names)
        should_add = isinstance(self.ast_node, ast.AugAssign)
        for name in lval_names:
            should_add_for_name = should_add or name in rval_names
            obj, scope = self.lookup_obj_by_name(name)
            self.safety.make_data_cell_for_obj(name, obj, rval_deps, scope, add=should_add_for_name)

    @property
    def has_lval(self):
        # TODO: expand to method calls, etc.
        return isinstance(self.ast_node, (
            ast.Assign, ast.AugAssign, ast.FunctionDef, ast.AsyncFunctionDef, ast.For
        ))
