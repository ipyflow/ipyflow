# -*- coding: future_annotations -*-
import ast
import logging
from typing import TYPE_CHECKING

from nbsafety.analysis.symbol_edges import get_symbol_edges
from nbsafety.analysis.utils import stmt_contains_lval
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.data_model.scope import NamespaceScope
from nbsafety.singletons import nbs, tracer
from nbsafety.tracing.mutation_event import ArgMutate, ListAppend, ListExtend, ListInsert
from nbsafety.tracing.symbol_resolver import resolve_rval_symbols, update_usage_info
from nbsafety.tracing.utils import match_container_obj_or_namespace_with_literal_nodes

if TYPE_CHECKING:
    from types import FrameType
    from typing import List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class TraceStatement:
    def __init__(self, frame: FrameType, stmt_node: ast.stmt):
        self.frame: FrameType = frame
        self.stmt_node = stmt_node
        self.class_scope: Optional[NamespaceScope] = None
        self.lambda_call_point_deps_done_once = False
        self.node_id_for_last_call: Optional[int] = None

    @property
    def lineno(self):
        return self.stmt_node.lineno

    @property
    def finished(self):
        return self.stmt_id in tracer().seen_stmts

    @property
    def stmt_id(self):
        return id(self.stmt_node)

    def _contains_lval(self):
        return stmt_contains_lval(self.stmt_node)

    def get_post_call_scope(self):
        old_scope = tracer().cur_frame_original_scope
        if isinstance(self.stmt_node, ast.ClassDef):
            # classes need a new scope before the ClassDef has finished executing,
            # so we make it immediately
            return old_scope.make_child_scope(self.stmt_node.name, obj=-1)

        if isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_name = self.stmt_node.name
        else:
            func_name = None
        func_sym = nbs().statement_to_func_cell.get(id(self.stmt_node), None)
        if func_sym is None:
            # TODO: brittle; assumes any user-defined and traceable function will always be present; is this safe?
            return old_scope
        if not func_sym.is_function:
            msg = 'got non-function symbol %s for name %s' % (func_sym.full_path, func_name)
            if nbs().is_develop:
                raise TypeError(msg)
            else:
                logger.warning(msg)
                return old_scope
        if not self.finished:
            func_sym.create_symbols_for_call_args()
        return func_sym.call_scope

    def _handle_assign_target_for_deps(
        self,
        target: ast.AST,
        deps: Set[DataSymbol],
        maybe_fixup_literal_namespace=False,
    ) -> None:
        # logger.error("upsert %s into %s", deps, tracer()._partial_resolve_ref(target))
        try:
            scope, name, obj, is_subscript = tracer().resolve_store_data_for_target(target, self.frame)
        except KeyError as e:
            # e.g., slices aren't implemented yet
            # use suppressed log level to avoid noise to user
            logger.info("Exception: %s", e)
            return
        upserted = scope.upsert_data_symbol_for_name(
            name, obj, deps, self.stmt_node, is_subscript=is_subscript,
        )
        logger.info("sym %s upserted to scope %s has parents %s", upserted, scope, upserted.parents)
        if maybe_fixup_literal_namespace:
            namespace_for_upsert = nbs().namespaces.get(id(obj), None)
            if namespace_for_upsert is not None and namespace_for_upsert.is_anonymous:
                namespace_for_upsert.scope_name = str(name)
                namespace_for_upsert.parent_scope = scope

    def _handle_store_target_tuple_unpack_from_deps(self, target: Union[ast.List, ast.Tuple], deps: Set[DataSymbol]):
        for inner_target in target.elts:
            if isinstance(inner_target, (ast.List, ast.Tuple)):
                self._handle_store_target_tuple_unpack_from_deps(inner_target, deps)
            else:
                self._handle_assign_target_for_deps(inner_target, deps)

    def _handle_starred_store_target(self, target: ast.Starred, inner_deps: List[Optional[DataSymbol]]):
        try:
            scope, name, obj, is_subscript = tracer().resolve_store_data_for_target(target, self.frame)
        except KeyError as e:
            # e.g., slices aren't implemented yet
            # use suppressed log level to avoid noise to user
            logger.info("Exception: %s", e)
            return
        ns = nbs().namespaces.get(id(obj), None)
        if ns is None:
            ns = NamespaceScope(obj, str(name), scope)
        for i, inner_dep in enumerate(inner_deps):
            deps = set() if inner_dep is None else {inner_dep}
            ns.upsert_data_symbol_for_name(i, inner_dep.obj, deps, self.stmt_node, is_subscript=True)
        scope.upsert_data_symbol_for_name(
            name,
            obj,
            set(),
            self.stmt_node,
            is_subscript=is_subscript,
        )

    def _handle_store_target_tuple_unpack_from_namespace(
        self, target: Union[ast.List, ast.Tuple], rhs_namespace: NamespaceScope
    ):
        saved_starred_node: Optional[ast.Starred] = None
        saved_starred_deps = []
        for (i, inner_dep), (_, inner_target) in match_container_obj_or_namespace_with_literal_nodes(rhs_namespace, target):
            if isinstance(inner_target, ast.Starred):
                saved_starred_node = inner_target
                saved_starred_deps.append(inner_dep)
                continue
            if inner_dep is None:
                inner_deps = set()
            else:
                inner_deps = {inner_dep}
            if isinstance(inner_target, (ast.List, ast.Tuple)):
                inner_namespace = nbs().namespaces.get(inner_dep.obj_id, None)
                if inner_namespace is None:
                    self._handle_store_target_tuple_unpack_from_deps(inner_target, inner_deps)
                else:
                    self._handle_store_target_tuple_unpack_from_namespace(inner_target, inner_namespace)
            else:
                self._handle_assign_target_for_deps(
                    inner_target,
                    inner_deps,
                    maybe_fixup_literal_namespace=True,
                )
        if saved_starred_node is not None:
            self._handle_starred_store_target(saved_starred_node, saved_starred_deps)

    def _handle_store_target(self, target: ast.AST, value: ast.AST, skip_namespace_check: bool = False):
        if isinstance(target, (ast.List, ast.Tuple)):
            rhs_namespace = (
                None if skip_namespace_check
                # next branch will always return None if skip_namespace_check is true,
                # but we skip it anyway just for the sake of explicitness
                else nbs().namespaces.get(tracer().saved_assign_rhs_obj_id, None)
            )
            if rhs_namespace is None:
                self._handle_store_target_tuple_unpack_from_deps(target, resolve_rval_symbols(value))
            else:
                self._handle_store_target_tuple_unpack_from_namespace(target, rhs_namespace)
        else:
            self._handle_assign_target_for_deps(
                target, resolve_rval_symbols(value), maybe_fixup_literal_namespace=True
            )

    def _handle_store(self, node: Union[ast.Assign, ast.For]):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                self._handle_store_target(target, node.value)
        elif isinstance(node, ast.For):
            self._handle_store_target(node.target, node.iter, skip_namespace_check=True)
        else:  # pragma: no cover
            raise TypeError('node type not supported for node: %s' % ast.dump(node))

    def _handle_delete(self):
        assert isinstance(self.stmt_node, ast.Delete)
        for target in self.stmt_node.targets:
            try:
                scope, name, _, is_subscript = tracer().resolve_del_data_for_target(target)
                scope.delete_data_symbol_for_name(name, is_subscript=is_subscript)
            except KeyError as e:
                # this will happen if, e.g., a __delitem__ triggered a call
                # logger.info("got key error while trying to handle %s: %s", ast.dump(self.stmt_node), e)
                logger.info('got key error: %s', e)

    def _make_lval_data_symbols(self):
        if isinstance(self.stmt_node, (ast.Assign, ast.For)):
            self._handle_store(self.stmt_node)
        else:
            self._make_lval_data_symbols_old()

    def _make_lval_data_symbols_old(self):
        symbol_edges = get_symbol_edges(self.stmt_node)
        should_overwrite = not isinstance(self.stmt_node, ast.AugAssign)
        is_function_def = isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef))
        is_class_def = isinstance(self.stmt_node, ast.ClassDef)
        is_import = isinstance(self.stmt_node, (ast.Import, ast.ImportFrom))
        if is_function_def or is_class_def:
            assert len(symbol_edges) == 1
            # assert not lval_symbol_refs.issubset(rval_symbol_refs)

        for target, dep_node in symbol_edges:
            rval_deps = resolve_rval_symbols(dep_node)
            logger.info('create edges from %s to %s', rval_deps, target)
            if is_class_def:
                assert self.class_scope is not None
                class_ref = self.frame.f_locals[self.stmt_node.name]
                self.class_scope.obj = class_ref
                nbs().namespaces[id(class_ref)] = self.class_scope
            try:
                scope, name, obj, is_subscript = tracer().resolve_store_data_for_target(target, self.frame)
                scope.upsert_data_symbol_for_name(
                    name, obj, rval_deps, self.stmt_node,
                    overwrite=should_overwrite,
                    is_subscript=is_subscript,
                    is_function_def=is_function_def,
                    is_import=is_import,
                    class_scope=self.class_scope,
                    propagate=not isinstance(self.stmt_node, ast.For)
                )
            except KeyError:
                logger.warning('keyerror for %s', ast.dump(target) if isinstance(target, ast.AST) else target)
            except Exception as e:
                logger.warning('exception while handling store: %s', e)
                pass

    def _handle_list_mutation(
        self,
        mutation_event: Union[ListAppend, ListExtend, ListInsert],
        mutated_obj_id: int,
        mutation_upsert_deps: Set[DataSymbol],
    ):
        namespace_scope = nbs().namespaces.get(mutated_obj_id, None)
        mutated_sym = nbs().get_first_full_symbol(mutated_obj_id)
        if mutated_sym is None:
            return
        mutated_obj = mutated_sym.obj
        if isinstance(mutation_event, (ListAppend, ListExtend)):
            for upsert_pos in range(
                mutation_event.orig_len if isinstance(mutation_event, ListExtend) else len(mutated_obj) - 1,
                len(mutated_obj)
            ):
                if namespace_scope is None:
                    namespace_scope = NamespaceScope(
                        mutated_obj,
                        mutated_sym.name,
                        parent_scope=mutated_sym.containing_scope
                    )
                logger.info(
                    "upsert %s to %s with deps %s", len(mutated_obj) - 1, namespace_scope, mutation_upsert_deps
                )
                namespace_scope.upsert_data_symbol_for_name(
                    upsert_pos,
                    mutated_obj[upsert_pos],
                    mutation_upsert_deps,
                    self.stmt_node,
                    overwrite=False,
                    is_subscript=True,
                    propagate=False,
                )
        elif isinstance(mutation_event, ListInsert):
            for idx in range(len(mutated_obj) - 1, mutation_event.insert_pos, -1):
                subsym = namespace_scope._subscript_data_symbol_by_name.pop(idx - 1, None)
                if subsym is None:
                    continue
                subsym.name = idx
                namespace_scope._subscript_data_symbol_by_name[idx] = subsym
                subsym.refresh()
            namespace_scope.upsert_data_symbol_for_name(
                mutation_event.insert_pos,
                mutated_obj[mutation_event.insert_pos],
                mutation_upsert_deps,
                self.stmt_node,
                overwrite=False,
                is_subscript=True,
                propagate=False,
            )

    def handle_dependencies(self):
        if not nbs().dependency_tracking_enabled:
            return
        for mutated_obj_id, mutation_event, mutation_arg_dsyms, mutation_arg_objs in tracer().mutations:
            propagate_to_namespace_descendents = not isinstance(mutation_event, (ListAppend, ListExtend, ListInsert))
            logger.info("mutation %s %s %s %s", mutated_obj_id, mutation_event, mutation_arg_dsyms, mutation_arg_objs)
            update_usage_info(mutation_arg_dsyms)
            if isinstance(mutation_event, ArgMutate):
                for mutated_sym in mutation_arg_dsyms:
                    if mutated_sym is None:
                        continue
                    # TODO: happens when module mutates args
                    #  should we add module as a dep in this case?
                    mutated_sym.update_deps(set(), overwrite=False, mutated=True)
                continue

            # NOTE: this next block is necessary to ensure that we add the argument as a namespace child
            # of the mutated symbol. This helps to avoid propagating through to dependency children that are
            # themselves namespace children.
            if isinstance(mutation_event, (ListAppend, ListExtend, ListInsert)):
                mutation_upsert_deps: Set[DataSymbol] = set()
                if isinstance(mutation_event, (ListAppend, ListInsert)):
                    mutation_arg_dsyms, mutation_upsert_deps = mutation_upsert_deps, mutation_arg_dsyms
                self._handle_list_mutation(mutation_event, mutated_obj_id, mutation_upsert_deps)
            update_usage_info(nbs().aliases[mutated_obj_id])
            for mutated_sym in nbs().aliases[mutated_obj_id]:
                mutated_sym.update_deps(
                    mutation_arg_dsyms,
                    overwrite=False,
                    mutated=True,
                    propagate_to_namespace_descendents=propagate_to_namespace_descendents,
                )
        if self._contains_lval():
            self._make_lval_data_symbols()
        elif isinstance(self.stmt_node, ast.Delete):
            self._handle_delete()
        else:
            # make sure usage timestamps get bumped
            resolve_rval_symbols(self.stmt_node)

    def finished_execution_hook(self):
        if self.finished:
            return
        tracer().seen_stmts.add(self.stmt_id)
        self.handle_dependencies()
        tracer().after_stmt_reset_hook()
