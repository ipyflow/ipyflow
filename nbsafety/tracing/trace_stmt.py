# -*- coding: utf-8 -*-
import ast
from contextlib import contextmanager
import logging
from typing import TYPE_CHECKING

from nbsafety.analysis import (
    AttrSubSymbolChain, get_symbol_edges, stmt_contains_lval
)
from nbsafety.data_model.scope import NamespaceScope
from nbsafety.tracing.mutation_event import MutationEvent

if TYPE_CHECKING:
    from types import FrameType
    from typing import List, Optional, Set
    from nbsafety.data_model.data_symbol import DataSymbol
    from nbsafety.data_model.scope import Scope
    from nbsafety.safety import NotebookSafety

logger = logging.getLogger(__name__)


class TraceStatement(object):
    def __init__(self, safety: 'NotebookSafety', frame: 'FrameType', stmt_node: 'ast.stmt', scope: 'Scope'):
        self.safety = safety
        self.frame = frame
        self.stmt_node = stmt_node
        self.scope = scope
        self.class_scope: Optional[NamespaceScope] = None
        self.call_point_deps: List[Set[DataSymbol]] = []
        self.lambda_call_point_deps_done_once = False
        self.call_seen = False

    @contextmanager
    def replace_active_scope(self, new_active_scope):
        old_scope = self.scope
        self.scope = new_active_scope
        yield
        self.scope = old_scope

    @property
    def finished(self):
        return self.stmt_id in self.safety.tracing_manager.seen_stmts

    @property
    def stmt_id(self):
        return id(self.stmt_node)

    def _contains_lval(self):
        return stmt_contains_lval(self.stmt_node)

    def compute_rval_dependencies(self, rval_symbol_refs=None):
        if rval_symbol_refs is None:
            symbol_edges, _ = get_symbol_edges(self.stmt_node)
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
            # else:
            #     assert not isinstance(name, AttrSubSymbolChain)
        return rval_data_symbols.union(*self.call_point_deps) | self.safety.tracing_manager.loaded_data_symbols

    def get_post_call_scope(self):
        old_scope = self.safety.tracing_manager.cur_frame_original_scope
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
        func_cell = self.safety.statement_to_func_cell.get(id(self.stmt_node), None)
        if func_cell is None:
            # TODO: brittle; assumes any user-defined and traceable function will always be present; is this safe?
            return old_scope
        if not func_cell.is_function:
            if self.safety.is_develop:
                raise TypeError('got non-function symbol %s for name %s' % (func_cell.full_path, func_name))
            else:
                # TODO: log an error to a file
                return old_scope
        if not self.finished:
            func_cell.create_symbols_for_call_args()
        return func_cell.call_scope

    def _handle_attrsub_stores(self, symbol_edges, deep_rval_deps):
        if len(symbol_edges) == 0:
            rval_deps = deep_rval_deps
        else:
            rval_deps = self.compute_rval_dependencies(
                rval_symbol_refs=set.union(*symbol_edges.values()) - {None}
            ) | deep_rval_deps
        for scope, obj, attr_or_sub, is_subscript in self.safety.tracing_manager.saved_store_data:
            try:
                attr_or_sub_obj = self.safety.retrieve_namespace_attr_or_sub(obj, attr_or_sub, is_subscript)
            except:
                continue
            should_overwrite = not isinstance(self.stmt_node, ast.AugAssign)
            scope_to_use = scope.get_earliest_ancestor_containing(id(attr_or_sub_obj), is_subscript)
            if scope_to_use is None:
                # Nobody before `scope` has it, so we'll insert it at this level
                scope_to_use = scope
            scope_to_use.upsert_data_symbol_for_name(
                attr_or_sub, attr_or_sub_obj, rval_deps, self.stmt_node, is_subscript,
                overwrite=should_overwrite, is_function_def=False, class_scope=None
            )
            # print(scope_to_use, 'upsert', attr_or_sub, attr_or_sub_obj, rval_deps)
            if len(self.safety.tracing_manager.saved_store_data) == 1:
                break
        else:
            return None, None

        return scope_to_use, attr_or_sub

    def _handle_literal_namespace(self, lval_name, rval_names, stored_attrsub_scope, stored_attrsub_name):
        # remaining_rval_names = set(rval_names)
        remaining_rval_names = rval_names
        if self.safety.tracing_manager.literal_namespace is None:
            return remaining_rval_names
        literal_namespace = self.safety.tracing_manager.literal_namespace
        self.safety.tracing_manager.literal_namespace = None
        if lval_name is None:
            if stored_attrsub_name is None:
                literal_namespace.scope_name = '<unknown namespace>'
            else:
                literal_namespace.scope_name = stored_attrsub_name
                if stored_attrsub_scope is not None:
                    literal_namespace.parent_scope = stored_attrsub_scope
        else:
            literal_namespace.scope_name = lval_name
        self.safety.namespaces[literal_namespace.obj_id] = literal_namespace

        # TODO: need tighter integration w/ assignment edges to allow for accurate drawing of edges to literal elements
        return remaining_rval_names
        # if len(rval_names) != literal_namespace.num_subscript_symbols:
        #     return remaining_rval_names
        #
        # # FIXME: rval_names can be traversed in the wrong order!
        # for rval_name, literal_namespace_symbol in zip(
        #         rval_names, literal_namespace.all_data_symbols_this_indentation(exclude_class=True, is_subscript=True)
        # ):
        #     if rval_name is None or rval_name == lval_name:
        #         continue
        #     literal_namespace_sym_parent = self.scope.lookup_data_symbol_by_name(rval_name)
        #     if literal_namespace_sym_parent is None:
        #         continue
        #     remaining_rval_names.discard(rval_name)
        #     literal_namespace_sym_parent.children.add(literal_namespace_symbol)
        #     literal_namespace_symbol.parents.add(literal_namespace_sym_parent)
        #
        # return remaining_rval_names

    def _make_lval_data_symbols(self):
        symbol_edges, should_overwrite = get_symbol_edges(self.stmt_node)
        deep_rval_deps = self._gather_deep_ref_rval_dsyms()
        is_function_def = isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef))
        is_class_def = isinstance(self.stmt_node, ast.ClassDef)
        is_import = isinstance(self.stmt_node, (ast.Import, ast.ImportFrom))
        if is_function_def or is_class_def:
            assert len(symbol_edges) == 1
            # assert not lval_symbol_refs.issubset(rval_symbol_refs)

        stored_attrsub_scope, stored_attrsub_name = self._handle_attrsub_stores(symbol_edges, deep_rval_deps)
        for lval_name, rval_names in symbol_edges.items():
            rval_names = self._handle_literal_namespace(
                lval_name, rval_names, stored_attrsub_scope, stored_attrsub_name
            )
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
                self.scope.upsert_data_symbol_for_name(
                    lval_name, obj, rval_deps, self.stmt_node, False,
                    overwrite=should_overwrite_for_name, is_function_def=is_function_def, is_import=is_import,
                    class_scope=self.class_scope, propagate=not isinstance(self.stmt_node, ast.For)
                )
            except KeyError:
                logger.warning('keyerror for %s', lval_name)
                pass

    def _gather_deep_ref_rval_dsyms(self):
        deep_ref_rval_dsyms = set()
        for deep_ref_obj_id, deep_ref_name, deep_ref_args in self.safety.tracing_manager.deep_refs:
            deep_ref_arg_dsyms = set()
            for arg in deep_ref_args:
                if isinstance(arg, str):
                    deep_ref_arg_dsyms.add(self.scope.lookup_data_symbol_by_name(arg))
                elif isinstance(arg, AttrSubSymbolChain):
                    deep_ref_arg_dsyms.add(self.scope.get_most_specific_data_symbol_for_attrsub_chain(arg)[0])
            deep_ref_arg_dsyms.discard(None)
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
        for mutated_obj_id, mutation_args, mutation_event in self.safety.tracing_manager.mutations:
            if mutation_event == MutationEvent.arg_mutate:
                for _, arg_id in mutation_args:
                    for mutated_sym in self.safety.aliases[arg_id]:
                        # TODO: happens when module mutates args
                        #  should we add module as a dep in this case?
                        mutated_sym.update_deps(set(), overwrite=False, mutated=True)
                continue

            mutation_arg_dsyms = set()
            for arg, _ in mutation_args:
                mutation_arg_dsyms.add(self.scope.get_most_specific_data_symbol_for_attrsub_chain(arg)[0])
            mutation_arg_dsyms.discard(None)

            # NOTE: this next block is necessary to ensure that we add the argument as a namespace child
            # of the mutated symbol. This helps to avoid propagating through to dependency children that are
            # themselves namespace children.
            if mutation_event == MutationEvent.list_append and len(mutation_arg_dsyms) == 1:
                namespace_scope = self.safety.namespaces.get(mutated_obj_id, None)
                mutated_obj_aliases = self.safety.aliases.get(mutated_obj_id, None)
                if mutated_obj_aliases is not None:
                    mutated_sym = next(iter(mutated_obj_aliases))
                    mutated_obj = mutated_sym._get_obj()
                    mutation_arg_sym = next(iter(mutation_arg_dsyms))
                    mutation_arg_obj = mutation_arg_sym._get_obj()
                    # TODO: replace int check w/ more general "immutable" check
                    if mutated_sym is not None and mutation_arg_obj is not None and not isinstance(mutation_arg_obj, int):
                        if namespace_scope is None:
                            namespace_scope = NamespaceScope(
                                mutated_obj, self.safety, mutated_sym.name,
                                parent_scope=mutated_sym.containing_scope
                            )
                        namespace_scope.upsert_data_symbol_for_name(
                            len(mutated_obj) - 1, mutation_arg_obj, set(), self.stmt_node,
                            is_subscript=True, overwrite=False, propagate=False
                        )
            # TODO: add mechanism for skipping namespace children in case of list append
            for mutated_sym in self.safety.aliases[mutated_obj_id]:
                mutated_sym.update_deps(mutation_arg_dsyms, overwrite=False, mutated=True)
        if self._contains_lval():
            self._make_lval_data_symbols()
        else:
            if len(self.safety.tracing_manager.saved_store_data) > 0 and self.safety.is_develop:
                logger.warning('saw unexpected state in saved_store_data: %s',
                               self.safety.tracing_manager.saved_store_data)

    def finished_execution_hook(self):
        if self.finished:
            return
        # print('finishing stmt', self.stmt_node)
        self.safety.tracing_manager.seen_stmts.add(self.stmt_id)
        self.handle_dependencies()
        self.safety.tracing_manager.after_stmt_reset_hook()
        self.safety._namespace_gc()
        # self.safety._gc()
