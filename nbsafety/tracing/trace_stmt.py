# -*- coding: utf-8 -*-
import ast
from contextlib import contextmanager
import logging
from typing import TYPE_CHECKING

from .dep_update import DependencyUpdate
from ..analysis import get_statement_symbol_edges
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
        self._marked_finished = False

    @contextmanager
    def replace_active_scope(self, new_active_scope):
        old_scope = self.scope
        self.scope = new_active_scope
        yield
        self.scope = old_scope

    @property
    def finished(self):
        return self._marked_finished
        # return self.marked_finished and isinstance(self.stmt_node, (ast.For, ast.Lambda))

    def compute_rval_dependencies(self, rval_symbol_refs=None):
        if rval_symbol_refs is None:
            symbol_edges, _ = get_statement_symbol_edges(self.stmt_node)
            if len(symbol_edges) == 0:
                rval_symbol_refs = set()
            else:
                rval_symbol_refs = set.union(*symbol_edges.values()) - {None}
        rval_data_symbols = set()
        for name in rval_symbol_refs:
            if name is None:
                continue
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
            raise TypeError('got non-function symbol %s for name %s' % (func_cell.full_path, func_name))
        return func_cell.call_scope

    def _gather_lval_data_symbol_dep_updates(self):
        symbol_edges, should_overwrite = get_statement_symbol_edges(self.stmt_node)
        deep_rval_deps = self._gather_deep_ref_rval_dsyms()
        is_function_def = isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef))
        is_class_def = isinstance(self.stmt_node, ast.ClassDef)
        if is_function_def or is_class_def:
            assert len(symbol_edges) == 1
            # assert not lval_symbol_refs.issubset(rval_symbol_refs)
        for lval_name, rval_names in symbol_edges.items():
            if lval_name is None:
                continue
            should_overwrite_for_name = should_overwrite and lval_name not in rval_names
            rval_deps = self.compute_rval_dependencies(rval_symbol_refs=rval_names - {lval_name}) | deep_rval_deps
            # print('create edges from', rval_deps, 'to', lval_name, should_overwrite_for_name)
            if is_class_def:
                assert self.class_scope is not None
                class_ref = self.frame.f_locals[self.stmt_node.name]
                class_obj_id = id(class_ref)
                self.class_scope.obj_id = class_obj_id
                self.safety.namespaces[class_obj_id] = self.class_scope
            # if is_function_def:
            #     print('create function', name, 'in scope', self.scope)
            try:
                obj = self.frame.f_locals[lval_name]
                dep_update = DependencyUpdate(rval_deps, should_overwrite_for_name)
                dsym = self.scope.upsert_data_symbol_for_name(
                    lval_name, obj, False, is_function_def=is_function_def, class_scope=self.class_scope,
                )
                self.safety.dep_updates[dsym] = self.safety.dep_updates[dsym].update(dep_update)
            except KeyError:
                pass
        if len(symbol_edges) == 0:
            rval_deps = deep_rval_deps
        else:
            rval_deps = self.compute_rval_dependencies(
                rval_symbol_refs=set.union(*symbol_edges.values()) - {None}
            ) | deep_rval_deps
        for scope, obj, attr_or_sub, is_subscript in self.safety.attr_trace_manager.saved_store_data:
            # print(scope, obj, attr_or_sub, is_subscript)
            try:
                attr_or_sub_obj = retrieve_namespace_attr_or_sub(obj, attr_or_sub, is_subscript)
            except (AttributeError, KeyError, IndexError):
                continue
            should_overwrite = not isinstance(self.stmt_node, ast.AugAssign)
            scope_to_use = scope.get_earliest_ancestor_containing(id(attr_or_sub_obj), is_subscript)
            if scope_to_use is None:
                # Nobody before `scope` has it, so we'll insert it at this level
                scope_to_use = scope
            old_dsym = scope_to_use.lookup_data_symbol_by_name_this_indentation(attr_or_sub)
            if old_dsym is not None and scope_to_use.is_globally_accessible:
                if attr_or_sub not in scope_to_use.data_symbol_by_name(is_subscript):
                    # In this case, we are copying from a class and we need the dsym from which we are copying
                    # as able to propagate to the new dsym.
                    # Example:
                    # class Foo:
                    #     shared = 99
                    # foo = Foo()
                    # foo.shared = 42  # old_dc refers to Foo.shared here
                    # Earlier, we were explicitly adding Foo.shared as a dependency of foo.shared as follows:
                    # deps.add(old_dc)
                    # But it turns out not to be necessary because foo depends on Foo, and changing Foo.shared will
                    # propagate up the namespace hierarchy to Foo, which propagates to foo, which then propagates to
                    # all of foo's namespace children (e.g. foo.shared).
                    # This raises the question of whether we should draw the foo <-> Foo edge, since irrelevant namespace
                    # children could then also be affected (e.g. some instance variable foo.x).
                    # Perhaps a better strategy is to prevent propagation along this edge unless class Foo is redeclared.
                    # If we do this, then we should go back to explicitly adding the dep as follows:
                    # EDIT: added check to avoid propagating along class -> instance edge when class not redefined, so now
                    # it is important to explicitly add this dep.
                    # deps.add(old_dc)
                    rval_deps.add(old_dsym)
            dep_update = DependencyUpdate(rval_deps, should_overwrite)
            dsym = scope_to_use.upsert_data_symbol_for_name(
                attr_or_sub, attr_or_sub_obj, is_subscript, is_function_def=False, class_scope=None
            )
            self.safety.dep_updates[dsym] = self.safety.dep_updates[dsym].update(dep_update)

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
            dep_update = DependencyUpdate(mutation_arg_dsyms, False, mutate=True)
            self.safety.dep_updates[mutated_obj_id] = self.safety.dep_updates[mutated_obj_id].update(dep_update)
        if self.has_lval:
            self._gather_lval_data_symbol_dep_updates()
        else:
            if len(self.safety.attr_trace_manager.saved_store_data) > 0:
                logger.warning('saw unexpected state in saved_store_data: %s',
                               self.safety.attr_trace_manager.saved_store_data)

    def finished_execution_hook(self):
        if self.finished:
            return
        # print('finishing stmt', self.stmt_node)
        self._marked_finished = True
        self.handle_dependencies()
        self.safety.attr_trace_manager.reset()

    @property
    def has_lval(self):
        # TODO: expand to method calls, etc.
        return isinstance(self.stmt_node, (
            ast.Assign, ast.AnnAssign, ast.AugAssign, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.For
        ))
