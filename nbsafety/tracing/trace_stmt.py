# -*- coding: utf-8 -*-
from __future__ import annotations
import ast
from typing import TYPE_CHECKING

from ..analysis import get_statement_lvals_and_rval_names
from ..data_cell import FunctionDataCell

if TYPE_CHECKING:
    from types import FrameType
    from typing import Set
    from ..data_cell import DataCell
    from ..safety import DependencySafety
    from ..scope import Scope


class TraceStatement(object):
    def __init__(self, safety: DependencySafety, frame: FrameType, stmt_node: ast.stmt, scope: Scope):
        self.safety = safety
        self.frame = frame
        self.stmt_node = stmt_node
        self.scope = scope
        self.extra_dependencies: Set[DataCell] = set()

    def compute_rval_dependencies(self, rval_names=None):
        if rval_names is None:
            _, rval_names = get_statement_lvals_and_rval_names(self.stmt_node)
        rval_data_cells = set()
        for name in rval_names:
            maybe_rval_dc = self.scope.lookup_data_cell_by_name(name)
            if maybe_rval_dc is not None:
                rval_data_cells.add(maybe_rval_dc)
        return rval_data_cells | self.extra_dependencies

    def get_post_call_scope(self, old_scope: Scope):
        if isinstance(self.stmt_node, ast.ClassDef):
            # classes need a new scope before the ClassDef has finished executing,
            # so we make it immediately
            return old_scope.make_child_scope(self.stmt_node.name)

        if not isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # TODO: probably the right thing is to check is whether a lambda appears somewhere inside the ast node
            # if not isinstance(self.ast_node, ast.Lambda):
            #     raise TypeError('unexpected type for ast node %s' % self.ast_node)
            return old_scope
        func_name = self.stmt_node.name
        func_cell = self.scope.lookup_data_cell_by_name(func_name)
        if func_cell is None:
            # TODO: brittle; assumes any user-defined and traceable function will always be present; is this safe?
            return old_scope
        if not isinstance(func_cell, FunctionDataCell):
            raise TypeError('got non-function data cell for name %s' % func_name)
        return func_cell.scope

    def make_lhs_data_cells_if_has_lval(self):
        if not self.has_lval:
            return
        if not self.safety.dependency_tracking_enabled:
            return
        lval_names, rval_names = get_statement_lvals_and_rval_names(self.stmt_node)
        rval_deps = self.compute_rval_dependencies(rval_names=rval_names - lval_names)
        is_function_def = isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef))
        is_class_def = isinstance(self.stmt_node, ast.ClassDef)
        should_add = isinstance(self.stmt_node, ast.AugAssign)
        if is_function_def or is_class_def:
            assert len(lval_names) == 1
            assert not lval_names.issubset(rval_names)
        for name in lval_names:
            should_add_for_name = should_add or name in rval_names
            self.scope.upsert_data_cell_for_name(
                name, rval_deps, add=should_add_for_name, is_function_def=is_function_def, is_class_def=is_class_def
            )

    def finished_execution_hook(self):
        # need to handle namespace cloning upon object creation still
        self.make_lhs_data_cells_if_has_lval()
        if isinstance(self.stmt_node, ast.ClassDef):
            class_ref = self.frame.f_locals[self.stmt_node.name]
            self.safety.namespaces[id(class_ref)] = self.scope

    @property
    def has_lval(self):
        # TODO: expand to method calls, etc.
        return isinstance(self.stmt_node, (
            ast.Assign, ast.AugAssign, ast.FunctionDef, ast.AsyncFunctionDef, ast.For
        ))
