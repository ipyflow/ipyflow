# -*- coding: utf-8 -*-
import ast
from contextlib import contextmanager
import logging
from typing import TYPE_CHECKING

from ..analysis import get_statement_lval_and_rval_symbols
from ..data_cell import FunctionDataCell

if TYPE_CHECKING:
    from types import FrameType
    from typing import List, Optional, Set
    from ..data_cell import DataCell
    from ..safety import DependencySafety
    from ..scope import Scope

logger = logging.getLogger(__name__)


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
            _, rval_symbols, _ = get_statement_lval_and_rval_symbols(self.stmt_node)
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

    def _get_obj_id_for_name(self, name):
        try:
            return id(self.frame.f_locals[name])
        except KeyError:
            logger.error('unable to find object for name %s', name)
            return id(None)

    def _handle_aliases(
            self,
            old_id: 'Optional[int]', old_dc: 'Optional[DataCell]',
            obj_id: 'Optional[int]', dc: 'Optional[DataCell]'
    ):
        if old_id is not None and old_dc is not None:
            self.safety.aliases[old_id].discard(old_dc)
        if obj_id is not None and dc is not None:
            self.safety.aliases[obj_id].add(dc)
        if old_id == obj_id:
            for alias_dc in self.safety.aliases[obj_id]:
                alias_dc.update_deps(set(), add=True)

    def _make_lval_data_cells(self):
        lval_symbols, rval_symbols, should_add = get_statement_lval_and_rval_symbols(self.stmt_node)
        rval_deps = self.compute_rval_dependencies(rval_symbols=rval_symbols - lval_symbols)
        is_function_def = isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef))
        is_class_def = isinstance(self.stmt_node, ast.ClassDef)
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
            obj_id = self._get_obj_id_for_name(name)
            dc, old_dc, old_id = self.scope.upsert_data_cell_for_name(
                name, obj_id, rval_deps,
                add=should_add_for_name, is_function_def=is_function_def, class_scope=self.class_scope
            )
            self._handle_aliases(old_id, old_dc, obj_id, dc)
        if len(self.safety.attr_trace_manager.saved_store_data) > 0:
            assert isinstance(self.stmt_node, (ast.Assign, ast.AnnAssign))
        if len(self.safety.attr_trace_manager.saved_aug_store_data) > 0:
            assert isinstance(self.stmt_node, ast.AugAssign)
        for scope, obj, attr in self.safety.attr_trace_manager.saved_store_data:
            obj_id = id(getattr(obj, attr, None))
            dc, old_dc, old_id = scope.upsert_data_cell_for_name(
                attr, obj_id, rval_deps, add=False, is_function_def=False, class_scope=None
            )
            self._handle_aliases(old_id, old_dc, obj_id, dc)
        for scope, obj, attr in self.safety.attr_trace_manager.saved_aug_store_data:
            obj_id = id(getattr(obj, attr, None))
            dc, old_dc, old_id = scope.upsert_data_cell_for_name(
                attr, obj_id, rval_deps, add=True, is_function_def=False, class_scope=None
            )
            self._handle_aliases(old_id, old_dc, obj_id, dc)

    def handle_dependencies(self):
        if not self.safety.dependency_tracking_enabled:
            return
        for mutated_obj_id, mutation_args in self.safety.attr_trace_manager.mutations:
            mutation_arg_dcs = set(self.scope.lookup_data_cell_by_name(arg) for arg in mutation_args) - {None}
            for mutated_dc in self.safety.aliases[mutated_obj_id]:
                mutated_dc.update_deps(mutation_arg_dcs, add=True)
        if self.has_lval:
            self._make_lval_data_cells()
        else:
            assert len(self.safety.attr_trace_manager.saved_store_data) == 0
            assert len(self.safety.attr_trace_manager.saved_aug_store_data) == 0

    def finished_execution_hook(self):
        if self.marked_finished:
            return
        # print('finishing stmt', self.stmt_node)
        self.marked_finished = True
        self.handle_dependencies()

    @property
    def has_lval(self):
        # TODO: expand to method calls, etc.
        return isinstance(self.stmt_node, (
            ast.Assign, ast.AnnAssign, ast.AugAssign, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.For
        ))
