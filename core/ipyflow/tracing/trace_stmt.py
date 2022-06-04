# -*- coding: utf-8 -*-
import ast
import logging
from types import FrameType
from typing import cast, List, Optional, Set, Union

import ipyflow.tracing.mutation_event as me
from ipyflow.analysis.symbol_edges import get_symbol_edges
from ipyflow.analysis.symbol_ref import SymbolRef
from ipyflow.analysis.utils import stmt_contains_lval
from ipyflow.data_model.data_symbol import DataSymbol
from ipyflow.data_model.namespace import Namespace
from ipyflow.data_model.scope import Scope
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.singletons import flow, tracer
from ipyflow.tracing.symbol_resolver import resolve_rval_symbols
from ipyflow.tracing.utils import match_container_obj_or_namespace_with_literal_nodes


try:
    import pandas
except ImportError:
    pandas = None


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class TraceStatement:
    def __init__(self, frame: FrameType, stmt_node: ast.stmt) -> None:
        self.frame: FrameType = frame
        self.stmt_node = stmt_node
        self.class_scope: Optional[Namespace] = None
        self.lambda_call_point_deps_done_once = False
        self.node_id_for_last_call: Optional[int] = None

    @property
    def lineno(self) -> int:
        return self.stmt_node.lineno

    @property
    def finished(self) -> bool:
        return self.stmt_id in tracer().seen_stmts

    @property
    def stmt_id(self) -> int:
        return id(self.stmt_node)

    def _contains_lval(self) -> bool:
        return stmt_contains_lval(self.stmt_node)

    def get_post_call_scope(self, call_frame: FrameType) -> Scope:
        old_scope = tracer().cur_frame_original_scope
        if isinstance(self.stmt_node, ast.ClassDef):
            # classes need a new scope before the ClassDef has finished executing,
            # so we make it immediately
            pending_ns = Namespace.make_child_namespace(old_scope, self.stmt_node.name)
            tracer().pending_class_namespaces.append(pending_ns)
            return pending_ns

        if isinstance(self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_name = self.stmt_node.name
        else:
            func_name = None
        func_sym = flow().statement_to_func_cell.get(id(self.stmt_node), None)
        if func_sym is None:
            # TODO: brittle; assumes any user-defined and traceable function will always be present; is this safe?
            return old_scope
        if not func_sym.is_function:
            msg = "got non-function symbol %s for name %s" % (
                func_sym.full_path,
                func_name,
            )
            if flow().is_develop:
                raise TypeError(msg)
            else:
                logger.warning(msg)
                return old_scope
        if not self.finished:
            func_sym.create_symbols_for_call_args(call_frame)
        return func_sym.call_scope

    @staticmethod
    def _handle_reactive_store(target: ast.AST) -> None:
        try:
            symbol_ref = SymbolRef(target)
            reactive_seen = False
            blocking_seen = False
            for resolved in symbol_ref.gen_resolved_symbols(
                tracer().cur_frame_original_scope,
                only_yield_final_symbol=False,
                yield_all_intermediate_symbols=True,
                inherit_reactivity=False,
                yield_in_reverse=True,
            ):
                if resolved.is_blocking:
                    blocking_seen = True
                if resolved.is_reactive and not blocking_seen:
                    flow().updated_deep_reactive_symbols.add(resolved.dsym)
                    reactive_seen = True
                    if resolved.is_cascading_reactive:
                        resolved.dsym.bump_cascading_reactive_cell_num()
                if reactive_seen and not blocking_seen:
                    flow().updated_reactive_symbols.add(resolved.dsym)
                if blocking_seen and resolved.dsym not in flow().updated_symbols:
                    flow().blocked_reactive_timestamps_by_symbol[
                        resolved.dsym
                    ] = flow().cell_counter()
        except TypeError:
            return

    def _handle_assign_target_for_deps(
        self,
        target: ast.AST,
        deps: Set[DataSymbol],
        maybe_fixup_literal_namespace: bool = False,
    ) -> None:
        # logger.error("upsert %s into %s", deps, tracer()._partial_resolve_ref(target))
        try:
            (
                scope,
                name,
                obj,
                is_subscript,
                excluded_deps,
            ) = tracer().resolve_store_data_for_target(target, self.frame)
        except KeyError:
            # e.g., slices aren't implemented yet
            # use suppressed log level to avoid noise to user
            if flow().is_develop:
                logger.warning(
                    "keyerror for %s",
                    ast.dump(target) if isinstance(target, ast.AST) else target,
                )
            # if flow().is_test:
            #     raise ke
            return
        subscript_vals_to_use = [is_subscript]
        if pandas is not None and scope.is_namespace_scope:
            namespace = cast(Namespace, scope)
            if (
                isinstance(namespace.obj, pandas.DataFrame)
                and name in namespace.obj.columns
            ):
                subscript_vals_to_use.append(not is_subscript)
        self._handle_reactive_store(target)
        for subscript_val in subscript_vals_to_use:
            upserted = scope.upsert_data_symbol_for_name(
                name,
                obj,
                deps - excluded_deps,
                self.stmt_node,
                is_subscript=subscript_val,
                symbol_node=target,
            )
            logger.info(
                "sym %s upserted to scope %s has parents %s",
                upserted,
                scope,
                upserted.parents,
            )
        if maybe_fixup_literal_namespace:
            namespace_for_upsert = flow().namespaces.get(id(obj), None)
            if namespace_for_upsert is not None and namespace_for_upsert.is_anonymous:
                namespace_for_upsert.scope_name = str(name)
                namespace_for_upsert.parent_scope = scope

    def _handle_store_target_tuple_unpack_from_deps(
        self, target: Union[ast.List, ast.Tuple], deps: Set[DataSymbol]
    ) -> None:
        for inner_target in target.elts:
            if isinstance(inner_target, (ast.List, ast.Tuple)):
                self._handle_store_target_tuple_unpack_from_deps(inner_target, deps)
            else:
                self._handle_assign_target_for_deps(inner_target, deps)

    def _handle_starred_store_target(
        self, target: ast.Starred, inner_deps: List[Optional[DataSymbol]]
    ) -> None:
        try:
            scope, name, obj, is_subscript, _ = tracer().resolve_store_data_for_target(
                target, self.frame
            )
        except KeyError as e:
            # e.g., slices aren't implemented yet
            # use suppressed log level to avoid noise to user
            logger.info("Exception: %s", e)
            return
        ns = flow().namespaces.get(id(obj), None)
        if ns is None:
            ns = Namespace(obj, str(name), scope)
        for i, inner_dep in enumerate(inner_deps):
            deps = set() if inner_dep is None else {inner_dep}
            ns.upsert_data_symbol_for_name(
                i, inner_dep.obj, deps, self.stmt_node, is_subscript=True
            )
        scope.upsert_data_symbol_for_name(
            name,
            obj,
            set(),
            self.stmt_node,
            is_subscript=is_subscript,
            symbol_node=target,
        )
        self._handle_reactive_store(target.value)

    def _handle_store_target_tuple_unpack_from_namespace(
        self, target: Union[ast.List, ast.Tuple], rhs_namespace: Namespace
    ) -> None:
        saved_starred_node: Optional[ast.Starred] = None
        saved_starred_deps = []
        for (i, inner_dep), (
            _,
            inner_target,
        ) in match_container_obj_or_namespace_with_literal_nodes(rhs_namespace, target):
            if isinstance(inner_target, ast.Starred):
                saved_starred_node = inner_target
                saved_starred_deps.append(inner_dep)
                continue
            if inner_dep is None:
                inner_deps = set()
            else:
                inner_deps = {inner_dep}
            if isinstance(inner_target, (ast.List, ast.Tuple)):
                inner_namespace = flow().namespaces.get(inner_dep.obj_id, None)
                if inner_namespace is None:
                    self._handle_store_target_tuple_unpack_from_deps(
                        inner_target, inner_deps
                    )
                else:
                    self._handle_store_target_tuple_unpack_from_namespace(
                        inner_target, inner_namespace
                    )
            else:
                self._handle_assign_target_for_deps(
                    inner_target,
                    inner_deps,
                    maybe_fixup_literal_namespace=True,
                )
        if saved_starred_node is not None:
            self._handle_starred_store_target(saved_starred_node, saved_starred_deps)

    def _handle_store_target(
        self, target: ast.AST, value: ast.AST, skip_namespace_check: bool = False
    ) -> None:
        if isinstance(target, (ast.List, ast.Tuple)):
            rhs_namespace = (
                None
                if skip_namespace_check
                # next branch will always return None if skip_namespace_check is true,
                # but we skip it anyway just for the sake of explicitness
                else flow().namespaces.get(id(tracer().saved_assign_rhs_obj), None)
            )
            if rhs_namespace is None:
                self._handle_store_target_tuple_unpack_from_deps(
                    target, resolve_rval_symbols(value)
                )
            else:
                self._handle_store_target_tuple_unpack_from_namespace(
                    target, rhs_namespace
                )
        else:
            self._handle_assign_target_for_deps(
                target,
                resolve_rval_symbols(value),
                maybe_fixup_literal_namespace=True,
            )

    def _handle_store(self, node: Union[ast.Assign, ast.For]) -> None:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                self._handle_store_target(target, node.value)
        elif isinstance(node, ast.For):
            self._handle_store_target(node.target, node.iter, skip_namespace_check=True)
        else:  # pragma: no cover
            raise TypeError("node type not supported for node: %s" % ast.dump(node))

    def _handle_delete(self) -> None:
        assert isinstance(self.stmt_node, ast.Delete)
        for target in self.stmt_node.targets:
            try:
                scope, obj, name, is_subscript = tracer().resolve_del_data_for_target(
                    target
                )
                scope.delete_data_symbol_for_name(name, is_subscript=is_subscript)
            except KeyError as e:
                # this will happen if, e.g., a __delitem__ triggered a call
                # logger.info("got key error while trying to handle %s: %s", ast.dump(self.stmt_node), e)
                logger.info("got key error: %s", e)

    def _make_lval_data_symbols(self) -> None:
        if isinstance(self.stmt_node, (ast.Assign, ast.For)):
            self._handle_store(self.stmt_node)
        else:
            self._make_lval_data_symbols_old()

    def _make_lval_data_symbols_old(self) -> None:
        symbol_edges = get_symbol_edges(self.stmt_node)
        should_overwrite = not isinstance(self.stmt_node, ast.AugAssign)
        is_function_def = isinstance(
            self.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef)
        )
        is_class_def = isinstance(self.stmt_node, ast.ClassDef)
        is_import = isinstance(self.stmt_node, (ast.Import, ast.ImportFrom))
        if is_function_def or is_class_def:
            assert len(symbol_edges) == 1
            # assert not lval_symbol_refs.issubset(rval_symbol_refs)

        for target, dep_node in symbol_edges:
            rval_deps = resolve_rval_symbols(dep_node)
            logger.info("create edges from %s to %s", rval_deps, target)
            if is_class_def:
                assert self.class_scope is not None
                class_ref = self.frame.f_locals[cast(ast.ClassDef, self.stmt_node).name]
                self.class_scope.obj = class_ref
                flow().namespaces[id(class_ref)] = self.class_scope
            try:
                (
                    scope,
                    name,
                    obj,
                    is_subscript,
                    excluded_deps,
                ) = tracer().resolve_store_data_for_target(target, self.frame)
                scope.upsert_data_symbol_for_name(
                    name,
                    obj,
                    rval_deps - excluded_deps,
                    self.stmt_node,
                    overwrite=should_overwrite,
                    is_subscript=is_subscript,
                    is_function_def=is_function_def,
                    is_import=is_import,
                    class_scope=self.class_scope,
                    propagate=not isinstance(self.stmt_node, ast.For),
                    symbol_node=target if isinstance(target, ast.AST) else None,
                )
                if isinstance(
                    self.stmt_node,
                    (
                        ast.FunctionDef,
                        ast.ClassDef,
                        ast.AsyncFunctionDef,
                        ast.Import,
                        ast.ImportFrom,
                    ),
                ):
                    self._handle_reactive_store(self.stmt_node)
                elif isinstance(target, ast.AST):
                    self._handle_reactive_store(target)
            except KeyError as ke:
                # e.g., slices aren't implemented yet
                # put logging behind flag to avoid noise to user
                if flow().is_develop:
                    logger.warning(
                        "keyerror for %s",
                        ast.dump(target) if isinstance(target, ast.AST) else target,
                    )
                # if flow().is_test:
                #     raise ke
            except ImportError:
                raise
            except Exception as e:
                logger.warning("exception while handling store: %s", e)
                if flow().is_test:
                    raise e

    # TODO: put this logic in each respective MutationEvent itself
    def _handle_specific_mutation_type(
        self,
        mutation_event: me.MutationEvent,
        mutated_obj_id: int,
        mutation_upsert_deps: Set[DataSymbol],
    ) -> None:
        namespace_scope = flow().namespaces.get(mutated_obj_id, None)
        mutated_sym = flow().get_first_full_symbol(mutated_obj_id)
        if mutated_sym is None:
            return
        mutated_obj = mutated_sym.obj
        if isinstance(mutation_event, (me.ListAppend, me.ListExtend)):
            for upsert_pos in range(
                mutation_event.orig_len
                if isinstance(mutation_event, me.ListExtend)
                else len(mutated_obj) - 1,
                len(mutated_obj),
            ):
                if namespace_scope is None:
                    namespace_scope = Namespace(
                        mutated_obj,
                        mutated_sym.name,
                        parent_scope=mutated_sym.containing_scope,
                    )
                logger.info(
                    "upsert %s to %s with deps %s",
                    len(mutated_obj) - 1,
                    namespace_scope,
                    mutation_upsert_deps,
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
        elif isinstance(mutation_event, me.ListInsert):
            assert mutated_obj is namespace_scope.obj
            namespace_scope.shuffle_symbols_upward_from(mutation_event.pos)
            namespace_scope.upsert_data_symbol_for_name(
                mutation_event.pos,
                mutated_obj[mutation_event.pos],
                mutation_upsert_deps,
                self.stmt_node,
                overwrite=False,
                is_subscript=True,
                propagate=True,
            )
        elif (
            isinstance(mutation_event, (me.ListPop, me.ListRemove))
            and mutation_event.pos is not None
        ):
            assert mutated_obj is namespace_scope.obj
            namespace_scope.delete_data_symbol_for_name(
                mutation_event.pos, is_subscript=True
            )
        elif isinstance(mutation_event, me.NamespaceClear):
            for name in sorted(
                (
                    dsym.name
                    for dsym in namespace_scope.all_data_symbols_this_indentation(
                        exclude_class=True, is_subscript=True
                    )
                ),
                reverse=True,
            ):
                namespace_scope.delete_data_symbol_for_name(name, is_subscript=True)

    def handle_dependencies(self) -> None:
        for (
            mutated_obj_id,
            mutation_event,
            mutation_arg_dsyms,
            mutation_arg_objs,
        ) in tracer().mutations:
            logger.info(
                "mutation %s %s %s %s",
                mutated_obj_id,
                mutation_event,
                mutation_arg_dsyms,
                mutation_arg_objs,
            )
            Timestamp.update_usage_info(mutation_arg_dsyms)
            if isinstance(mutation_event, me.ArgMutate):
                for mutated_sym in mutation_arg_dsyms:
                    if mutated_sym is None or mutated_sym.is_anonymous:
                        continue
                    # TODO: happens when module mutates args
                    #  should we add module as a dep in this case?
                    mutated_sym.update_deps(set(), overwrite=False, mutated=True)
                continue

            # NOTE: this next block is necessary to ensure that we add the argument as a namespace child
            # of the mutated symbol. This helps to avoid propagating through to dependency children that are
            # themselves namespace children.
            should_propagate = True
            if not isinstance(
                mutation_event,
                (me.MutatingMethodEventNotYetImplemented, me.StandardMutation),
            ):
                should_propagate = False
                mutation_upsert_deps: Set[DataSymbol] = set()
                if isinstance(mutation_event, (me.ListAppend, me.ListInsert)):
                    mutation_arg_dsyms, mutation_upsert_deps = (
                        mutation_upsert_deps,
                        mutation_arg_dsyms,
                    )
                self._handle_specific_mutation_type(
                    mutation_event, mutated_obj_id, mutation_upsert_deps
                )
            Timestamp.update_usage_info(flow().aliases[mutated_obj_id])
            for mutated_sym in flow().aliases[mutated_obj_id]:
                mutated_sym.update_deps(
                    mutation_arg_dsyms,
                    overwrite=False,
                    mutated=True,
                    propagate_to_namespace_descendents=should_propagate,
                    refresh=should_propagate,
                )
        if self._contains_lval():
            self._make_lval_data_symbols()
        elif isinstance(self.stmt_node, ast.Delete):
            self._handle_delete()
        else:
            # make sure usage timestamps get bumped
            resolve_rval_symbols(self.stmt_node)

    def finished_execution_hook(self) -> None:
        if self.finished:
            return
        tracer().seen_stmts.add(self.stmt_id)
        self.handle_dependencies()
        tracer().after_stmt_reset_hook()
