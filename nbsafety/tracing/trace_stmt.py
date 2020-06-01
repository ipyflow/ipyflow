# -*- coding: utf-8 -*-
import ast
from contextlib import contextmanager
import logging
from typing import TYPE_CHECKING

from ..analysis import get_statement_lval_and_rval_symbol_refs
from ..utils import retrieve_namespace_attr_or_sub

if TYPE_CHECKING:
    from types import FrameType
    from typing import List, Optional, Set
    from ..data_symbol import DataSymbol
    from ..safety import DependencySafety
    from ..scope import Scope, NamespaceScope

logger = logging.getLogger(__name__)


class TraceStatement(object):
    def __init__(self, safety: 'DependencySafety', frame: 'FrameType', stmt_node: 'ast.stmt', scope: 'Scope'):
        self.safety = safety
        self.frame = frame
        self.stmt_node = stmt_node
        self.scope = scope
        self.class_scope: Optional[NamespaceScope] = None
        self.call_point_deps: List[Set[DataSymbol]] = []
        self.marked_finished = False

    @contextmanager
    def replace_active_scope(self, new_active_scope):
        old_scope = self.scope
        self.scope = new_active_scope
        yield
        self.scope = old_scope

    def compute_rval_dependencies(self, rval_symbol_refs=None):
        if rval_symbol_refs is None:
            _, rval_symbol_refs, _ = get_statement_lval_and_rval_symbol_refs(self.stmt_node)
        rval_data_symbols = set()
        for name in rval_symbol_refs:
            maybe_rval_dsym = self.scope.lookup_data_symbol_by_name(name)
            if maybe_rval_dsym is not None:
                rval_data_symbols.add(maybe_rval_dsym)
        return rval_data_symbols.union(*self.call_point_deps) | self.safety.attr_trace_manager.loaded_data_symbols

    def get_post_call_scope(self, old_scope: 'Scope'):
        if isinstance(self.stmt_node, ast.ClassDef):
            # classes need a new scope before the ClassDef has finished executing,
            # so we make it immediately
            return self.scope.make_child_scope(self.stmt_node.name, obj_id=-1)

        if not isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # TODO: probably the right thing is to check is whether a lambda appears somewhere inside the ast node
            # if not isinstance(self.ast_node, ast.Lambda):
            #     raise TypeError('unexpected type for ast node %s' % self.ast_node)
            return old_scope
        func_name = self.stmt_node.name
        func_cell = self.scope.lookup_data_symbol_by_name(func_name)
        if func_cell is None:
            # TODO: brittle; assumes any user-defined and traceable function will always be present; is this safe?
            return old_scope
        if not func_cell.is_function:
            raise TypeError('got non-function symbol %s for name %s' % (func_cell, func_name))
        return func_cell.call_scope

    def _make_lval_data_symbols(self):
        lval_symbol_refs, rval_symbol_refs, should_overwrite = get_statement_lval_and_rval_symbol_refs(self.stmt_node)
        rval_deps = self.compute_rval_dependencies(rval_symbol_refs=rval_symbol_refs - lval_symbol_refs)
        rval_deps |= self._gather_deep_ref_rval_dsyms()
        is_function_def = isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef))
        is_class_def = isinstance(self.stmt_node, ast.ClassDef)
        if is_function_def or is_class_def:
            assert len(lval_symbol_refs) == 1
            assert not lval_symbol_refs.issubset(rval_symbol_refs)
        for name in lval_symbol_refs:
            should_overwrite_for_name = should_overwrite and name not in rval_symbol_refs
            if is_class_def:
                assert self.class_scope is not None
                class_ref = self.frame.f_locals[self.stmt_node.name]
                class_obj_id = id(class_ref)
                self.class_scope.obj_id = class_obj_id
                self.safety.namespaces[class_obj_id] = self.class_scope
            # if is_function_def:
            #     print('create function', name, 'in scope', self.scope)
            try:
                obj = self.frame.f_locals[name]
                self.scope.upsert_data_symbol_for_name(
                    name, obj, rval_deps, False,
                    overwrite=should_overwrite_for_name, is_function_def=is_function_def, class_scope=self.class_scope,
                )
            except KeyError:
                pass
        for scope, obj, attr_or_sub, is_subscript in self.safety.attr_trace_manager.saved_store_data:
            # print(scope, obj, attr_or_sub, is_subscript)
            try:
                attr_or_sub_obj = retrieve_namespace_attr_or_sub(obj, attr_or_sub, is_subscript)
            except (AttributeError, KeyError, IndexError):
                continue
            should_overwrite = not isinstance(self.stmt_node, ast.AugAssign)
            # TODO: walk up the namespace hierarchy and check for a namespace scope w/ id of attr_or_sub_obj
            #  if we find it, use the containing scope instead of this scope to avoid cyclic namespaces
            scope.upsert_data_symbol_for_name(
                attr_or_sub, attr_or_sub_obj, rval_deps, is_subscript,
                overwrite=should_overwrite, is_function_def=False, class_scope=None
            )

    def _gather_deep_ref_rval_dsyms(self):
        deep_ref_rval_dsyms = set()
        for deep_ref_obj_id, deep_ref_name, deep_ref_args in self.safety.attr_trace_manager.deep_refs:
            deep_ref_arg_dsyms = set(self.scope.lookup_data_symbol_by_name(arg) for arg in deep_ref_args) - {None}
            deep_ref_rval_dsyms |= deep_ref_arg_dsyms
            if deep_ref_name is None:
                deep_ref_rval_dsyms |= self.safety.aliases.get(deep_ref_obj_id, set())
            else:
                deep_ref_dc = self.scope.lookup_data_symbol_by_name(deep_ref_name)
                if deep_ref_dc is not None and deep_ref_dc.obj_id == deep_ref_obj_id:
                    deep_ref_rval_dsyms.add(deep_ref_dc)
                else:
                    deep_ref_rval_dsyms |= self.safety.aliases.get(deep_ref_obj_id, set())
        return deep_ref_rval_dsyms

    def handle_dependencies(self):
        if not self.safety.dependency_tracking_enabled:
            return
        for mutated_obj_id, mutation_args in self.safety.attr_trace_manager.mutations:
            mutation_arg_dsyms = set(self.scope.lookup_data_symbol_by_name(arg) for arg in mutation_args) - {None}
            for mutated_dc in self.safety.aliases[mutated_obj_id]:
                mutated_dc.update_deps(mutation_arg_dsyms, overwrite=False, mutated=True)
        if self.has_lval:
            self._make_lval_data_symbols()
        else:
            # if len(self.safety.attr_trace_manager.saved_store_data) > 0:
            #     print(self.safety.attr_trace_manager.saved_store_data)
            assert len(self.safety.attr_trace_manager.saved_store_data) == 0

    def finished_execution_hook(self):
        if self.marked_finished:
            return
        # print('finishing stmt', self.stmt_node)
        self.marked_finished = True
        self.handle_dependencies()
        self.safety.attr_trace_manager.reset()
        self.safety.namespace_gc()

    @property
    def has_lval(self):
        # TODO: expand to method calls, etc.
        return isinstance(self.stmt_node, (
            ast.Assign, ast.AnnAssign, ast.AugAssign, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.For
        ))
