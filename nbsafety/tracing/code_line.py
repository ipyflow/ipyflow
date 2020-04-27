# -*- coding: utf-8 -*-
from __future__ import annotations
import ast

from ..analysis.rvalues import get_all_rval_names


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
    def __init__(self, text, ast_node, lineno, call_depth, frame):
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

    def compute_rval_dependencies(self, safety):
        rval_names = get_all_rval_names(self.ast_node)
        rval_data_cells = set()
        for name in rval_names:
            obj, _ = self.lookup_obj_by_name(name)
            rval_data_cells.add(safety.data_cell_by_ref[id(obj)])
        return rval_data_cells | self.extra_dependencies

    def make_lhs_data_cell(self, safety):
        if not isinstance(self.ast_node, ast.Assign):
            raise TypeError('Assign only supported for now')
        # TODO: support multiple targets
        target = self.ast_node.targets[0]
        while isinstance(target, ast.Subscript):
            target = target.value
        if not isinstance(target, ast.Name):
            raise TypeError('Expected ast.Name')
        lhs_name = target.id
        lhs_obj, scope = self.lookup_obj_by_name(lhs_name)
        safety.make_data_cell_for_obj(lhs_name, lhs_obj, self.compute_rval_dependencies(safety), scope)

    @property
    def has_lval(self):
        # TODO: expand to AugAssign, method calls, etc.
        return isinstance(self.ast_node, ast.Assign)
