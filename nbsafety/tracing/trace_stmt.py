# -*- coding: utf-8 -*-
import ast
from contextlib import contextmanager
from typing import TYPE_CHECKING

from ..analysis import get_statement_lval_and_rval_symbols
from ..data_cell import FunctionDataCell

if TYPE_CHECKING:
    from types import FrameType
    from typing import List, Optional, Set
    from ..data_cell import DataCell
    from ..safety import DependencySafety
    from ..scope import Scope


class TraceStatement(object):
    def __init__(self, safety: 'DependencySafety', frame: 'FrameType', stmt_node: 'ast.stmt', scope: 'Scope'):
        self.safety = safety
        self.frame = frame
        self.stmt_node = stmt_node
        self.scope = scope
        self.class_scope: Optional[Scope] = None
        self.call_point_dependencies: List[Set[DataCell]] = []
        self.marked_finished = False

    @contextmanager
    def replace_active_scope(self, new_active_scope):
        old_scope = self.scope
        self.scope = new_active_scope
        yield
        self.scope = old_scope

    def compute_rval_dependencies(self, rval_symbols=None):
        if rval_symbols is None:
            _, rval_symbols = get_statement_lval_and_rval_symbols(self.stmt_node)
        rval_data_cells = set()
        for name in rval_symbols:
            maybe_rval_dc = self.scope.lookup_data_cell_by_name(name)
            if maybe_rval_dc is not None:
                rval_data_cells.add(maybe_rval_dc)
        return rval_data_cells.union(*self.call_point_dependencies) | self.safety.attr_trace_manager.loaded_data_cells

    def get_post_call_scope(self, old_scope: 'Scope'):
        if isinstance(self.stmt_node, ast.ClassDef):
            # classes need a new scope before the ClassDef has finished executing,
            # so we make it immediately
            return self.scope.make_child_scope(self.stmt_node.name, is_namespace_scope=True)

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
            raise TypeError('got non-function data cell %s for name %s' % (func_cell, func_name))
        return func_cell.scope

    def make_lhs_data_cells_if_has_lval(self):
        if not self.has_lval:
            assert len(self.safety.attr_trace_manager.stored_scope_qualified_names) == 0
            assert len(self.safety.attr_trace_manager.aug_stored_scope_qualified_names) == 0
            return
        if not self.safety.dependency_tracking_enabled:
            return
        lval_symbols, rval_symbols = get_statement_lval_and_rval_symbols(self.stmt_node)
        rval_deps = self.compute_rval_dependencies(rval_symbols=rval_symbols - lval_symbols)
        is_function_def = isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef))
        is_class_def = isinstance(self.stmt_node, ast.ClassDef)
        should_add = isinstance(self.stmt_node, ast.AugAssign)
        if is_function_def or is_class_def:
            assert len(lval_symbols) == 1
            assert not lval_symbols.issubset(rval_symbols)
        for name in lval_symbols:
            should_add_for_name = should_add or name in rval_symbols
            if is_class_def:
                assert self.class_scope is not None
                class_ref = self.frame.f_locals[self.stmt_node.name]
                self.safety.namespaces[id(class_ref)] = self.class_scope
            # if is_function_def:
            #     print('create function', name, 'in scope', self.scope)
            self.scope.upsert_data_cell_for_name(
                name, rval_deps, add=should_add_for_name, is_function_def=is_function_def, class_scope=self.class_scope
            )
        if len(self.safety.attr_trace_manager.stored_scope_qualified_names) > 0:
            assert isinstance(self.stmt_node, ast.Assign)
        if len(self.safety.attr_trace_manager.aug_stored_scope_qualified_names) > 0:
            assert isinstance(self.stmt_node, ast.AugAssign)
        for scope, name in self.safety.attr_trace_manager.stored_scope_qualified_names:
            scope.upsert_data_cell_for_name(name, rval_deps, add=False, is_function_def=False, class_scope=None)
        for scope, name in self.safety.attr_trace_manager.aug_stored_scope_qualified_names:
            scope.upsert_data_cell_for_name(name, rval_deps, add=True, is_function_def=False, class_scope=None)

    def finished_execution_hook(self):
        if self.marked_finished:
            return
        # print('finishing stmt', self.stmt_node)
        self.marked_finished = True
        self.make_lhs_data_cells_if_has_lval()

    @property
    def has_lval(self):
        # TODO: expand to method calls, etc.
        return isinstance(self.stmt_node, (
            ast.Assign, ast.AugAssign, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.For
        ))
