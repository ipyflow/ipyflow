# -*- coding: utf-8 -*-
import ast
import builtins
import logging
import sys
from types import FrameType
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Union, cast

from ipyflow.analysis.live_refs import stmt_contains_cascading_reactive_rval
from ipyflow.analysis.symbol_edges import get_symbol_edges
from ipyflow.analysis.symbol_ref import SymbolRef
from ipyflow.analysis.utils import stmt_contains_lval
from ipyflow.data_model import DUPED_ATTRSUB_CLASSES
from ipyflow.data_model.namespace import Namespace
from ipyflow.data_model.scope import Scope
from ipyflow.data_model.symbol import Symbol
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.models import _StatementContainer, cells, statements
from ipyflow.singletons import flow, tracer
from ipyflow.slicing.context import SlicingContext, static_slicing_context
from ipyflow.slicing.mixin import SlicingMixin
from ipyflow.tracing.symbol_resolver import resolve_rval_symbols
from ipyflow.tracing.utils import match_container_obj_or_namespace_with_literal_nodes
from ipyflow.types import IdType, TimestampOrCounter

if TYPE_CHECKING:
    import astunparse
elif hasattr(ast, "unparse"):
    astunparse = ast
else:
    import astunparse

if TYPE_CHECKING:
    from ipyflow.data_model.code_cell import CodeCell


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


# just want to get rid of unused warning
_override_unused_warning_stmts = statements


class Statement(SlicingMixin):
    _TEXT_REPR_MAX_LENGTH: int = 70
    _stmts_by_ts: Dict[Timestamp, List["Statement"]] = {}
    _stmts_by_id: Dict[IdType, List["Statement"]] = {}

    def __init__(
        self,
        stmt_node: ast.stmt,
        frame: Optional[FrameType] = None,
        timestamp: Optional[Timestamp] = None,
        prev_stmt: Optional["Statement"] = None,
    ) -> None:
        self.stmt_node: ast.stmt = stmt_node
        self.frame: Optional[FrameType] = frame
        self._timestamp = timestamp or Timestamp.current()
        self._finished: bool = False
        self.prev_stmt = prev_stmt
        self.class_scope: Optional[Namespace] = None
        self.lambda_call_point_deps_done_once = False
        self.node_id_for_last_call: Optional[int] = None
        self._stmt_contains_cascading_reactive_rval: Optional[bool] = None
        self._dynamic_parents: Dict[IdType, Set[Symbol]] = {}
        self._dynamic_children: Dict[IdType, Set[Symbol]] = {}
        self._static_parents: Dict[IdType, Set[Symbol]] = {}
        self._static_children: Dict[IdType, Set[Symbol]] = {}

    @property
    def id(self) -> IdType:
        return self.stmt_id

    @property
    def timestamp(self) -> Timestamp:
        return self._timestamp

    @property
    def prev(self) -> Optional["Statement"]:
        return self.prev_stmt

    @property
    def text(self) -> str:
        if isinstance(self.stmt_node, ast.Assign) and self.stmt_node.lineno == max(
            getattr(nd, "lineno", self.stmt_node.lineno)
            for nd in ast.walk(self.stmt_node)
        ):
            components = []
            for node in self.stmt_node.targets + [self.stmt_node.value]:
                components.append(astunparse.unparse(node).strip())
                components[-1] = self._strip_tuple_parens(node, components[-1])
            return " = ".join(components).strip()
        else:
            return astunparse.unparse(self.stmt_node).strip()

    @staticmethod
    def _strip_tuple_parens(node: ast.AST, text: str) -> str:
        if (
            isinstance(node, (ast.BinOp, ast.Tuple))
            and len(text) >= 2
            and text[0] == "("
            and text[-1] == ")"
        ):
            return text[1:-1]
        else:
            return text

    @classmethod
    def create_and_track(
        cls,
        stmt_node: ast.stmt,
        frame: Optional[FrameType] = None,
        timestamp: Optional[Timestamp] = None,
        override: bool = False,
    ) -> "Statement":
        stmt_id = id(stmt_node)
        prev_stmt = cls.from_id(stmt_id) if cls.has_id(stmt_id) else None
        stmt = cls(stmt_node, frame=frame, timestamp=timestamp, prev_stmt=prev_stmt)
        if override and cls._stmts_by_ts.get(timestamp):
            prev = cls.at_timestamp(timestamp)
            all_with_prev_id = cls._stmts_by_id.pop(prev.id)
            assert len(all_with_prev_id) == 1
            assert not cls.has_id(stmt.stmt_id)
            cls._stmts_by_ts[stmt.timestamp] = [stmt]
            cls._stmts_by_id[stmt.stmt_id] = [stmt]
            for _ in SlicingContext.iter_slicing_contexts():
                for cid in list(prev.children.keys()):
                    cls.from_id(cid).replace_parent_edges(prev, stmt)
                for pid in list(prev.parents.keys()):
                    cls.from_id(pid).replace_child_edges(prev, stmt)
        else:
            cls._stmts_by_ts.setdefault(stmt.timestamp, []).append(stmt)
            cls._stmts_by_id.setdefault(stmt.stmt_id, []).append(stmt)
        with static_slicing_context():
            for parent, syms in (
                flow().stmt_deferred_static_parents.get(stmt.timestamp, {}).items()
            ):
                stmt.add_parent_edges(parent, syms)
        flow().stmt_deferred_static_parents.pop(stmt.timestamp, None)
        return stmt

    @classmethod
    def clear(cls):
        cls._stmts_by_ts = {}

    @classmethod
    def at_timestamp(
        cls, ts: TimestampOrCounter, stmt_num: Optional[int] = None
    ) -> "Statement":
        assert isinstance(ts, Timestamp) or stmt_num is not None
        if isinstance(ts, Timestamp):
            ts_to_use = ts
        else:
            ts_to_use = Timestamp(ts, stmt_num)
        return cls._stmts_by_ts[ts_to_use][0]

    @classmethod
    def from_id(cls, stmt_id: IdType) -> "Statement":
        return cls._stmts_by_id[stmt_id][0]

    @classmethod
    def from_id_nullable(cls, stmt_id: IdType) -> Optional["Statement"]:
        return cls._stmts_by_id.get(stmt_id, [None])[0]

    @classmethod
    def has_id(cls, stmt_id: IdType) -> bool:
        return len(cls._stmts_by_id.get(stmt_id, [])) > 0

    @classmethod
    def all_at_timestamp(cls, ts: Timestamp) -> List["Statement"]:
        return cls._stmts_by_ts.get(ts, [])

    @property
    def containing_cell(self) -> "CodeCell":
        return cells().at_timestamp(self.timestamp)

    @property
    def lineno(self) -> int:
        return self.stmt_node.lineno

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def stmt_id(self) -> int:
        return id(self.stmt_node)

    def __str__(self):
        return self.text

    def __repr__(self):
        return f"<{self.__class__.__name__}[ts={self.timestamp},text={repr(self.text[:self._TEXT_REPR_MAX_LENGTH])}]>"

    @property
    def stmt_contains_cascading_reactive_rval(self) -> bool:
        if self._stmt_contains_cascading_reactive_rval is None:
            self._stmt_contains_cascading_reactive_rval = (
                stmt_contains_cascading_reactive_rval(self.stmt_node)
            )
        return self._stmt_contains_cascading_reactive_rval

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
        func_sym = tracer().calling_symbol
        if func_sym is None or func_sym.call_scope is None:
            func_sym = flow().statement_to_func_sym.get(id(self.stmt_node), None)
        if func_sym is None:
            # TODO: brittle; assumes any user-defined and traceable function will always be present; is this safe?
            return old_scope
        if func_sym.call_scope is None:
            msg = "got non-function symbol %s for name %s" % (
                func_sym.full_path,
                func_name,
            )
            if flow().is_dev_mode:
                raise TypeError(msg)
            else:
                logger.warning(msg)
                return old_scope
        if not self.finished:
            prev_call_scope = func_sym.call_scope
            # we need a new scope upon call to prevent picking up outer scope's overwritten nonlocals
            new_call_scope = prev_call_scope.parent_scope.make_child_scope(
                func_sym.name
            )
            if prev_call_scope.symtab is not None:
                # we need to keep the previous call scope's symtab since it came from the function's containing scope
                new_call_scope.symtab = prev_call_scope.symtab
            func_sym.call_scope = new_call_scope
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
                    if resolved.is_cascading_reactive:
                        flow().updated_deep_reactive_symbols.add(resolved.dsym)
                    else:
                        flow().updated_deep_reactive_symbols_last_cell.add(
                            resolved.dsym
                        )
                    reactive_seen = True
                    if not resolved.is_live and resolved.atom.is_cascading_reactive:
                        resolved.dsym.bump_cascading_reactive_cell_num()
                if reactive_seen and not blocking_seen:
                    if resolved.is_cascading_reactive:
                        flow().updated_reactive_symbols.add(resolved.dsym)
                    else:
                        flow().updated_reactive_symbols_last_cell.add(resolved.dsym)
                if blocking_seen and resolved.dsym not in flow().updated_symbols:
                    flow().blocked_reactive_timestamps_by_symbol[
                        resolved.dsym
                    ] = flow().cell_counter()
        except TypeError:
            return

    def _handle_assign_target_for_deps(
        self,
        target: ast.AST,
        deps: Set[Symbol],
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
            if flow().is_dev_mode:
                logger.warning(
                    "keyerror for %s",
                    ast.dump(target) if isinstance(target, ast.AST) else target,
                )
            # if flow().is_test:
            #     raise ke
            return
        subscript_vals_to_use = [is_subscript]
        if scope.is_namespace_scope:
            namespace = cast(Namespace, scope)
            for modname, classname in DUPED_ATTRSUB_CLASSES:
                module = sys.modules.get(modname)
                if module is None:
                    continue
                clazz = getattr(module, classname, None)
                if clazz is None:
                    continue
                if isinstance(namespace.obj, clazz) and name in namespace.obj.columns:
                    subscript_vals_to_use.append(not is_subscript)
                    break
        for subscript_val in subscript_vals_to_use:
            upserted = scope.upsert_data_symbol_for_name(
                name,
                obj,
                deps - excluded_deps,
                self.stmt_node,
                is_subscript=subscript_val,
                symbol_node=target,
                is_cascading_reactive=self.stmt_contains_cascading_reactive_rval,
            )
            logger.info(
                "sym %s upserted to scope %s has parents %s",
                upserted,
                scope,
                upserted.parents,
            )
        self._handle_reactive_store(target)
        if maybe_fixup_literal_namespace:
            namespace_for_upsert = flow().namespaces.get(id(obj), None)
            if namespace_for_upsert is not None and namespace_for_upsert.is_anonymous:
                namespace_for_upsert.scope_name = str(name)
                namespace_for_upsert.parent_scope = scope

    def _handle_store_target_tuple_unpack_from_deps(
        self, target: Union[ast.List, ast.Tuple], deps: Set[Symbol]
    ) -> None:
        for inner_target in target.elts:
            if isinstance(inner_target, (ast.List, ast.Tuple)):
                self._handle_store_target_tuple_unpack_from_deps(inner_target, deps)
            else:
                self._handle_assign_target_for_deps(inner_target, deps)

    def _handle_starred_store_target(
        self, target: ast.Starred, inner_deps: List[Optional[Symbol]]
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
            ns = Namespace(obj, str(name), parent_scope=scope)
        for i, inner_dep in enumerate(inner_deps):
            deps = set() if inner_dep is None else {inner_dep}
            ns.upsert_data_symbol_for_name(
                i,
                inner_dep.obj,
                deps,
                self.stmt_node,
                is_subscript=True,
                is_cascading_reactive=self.stmt_contains_cascading_reactive_rval,
            )
        scope.upsert_data_symbol_for_name(
            name,
            obj,
            set(),
            self.stmt_node,
            is_subscript=is_subscript,
            symbol_node=target,
            is_cascading_reactive=self.stmt_contains_cascading_reactive_rval,
        )
        self._handle_reactive_store(target.value)

    def _handle_store_target_tuple_unpack_from_namespace(
        self,
        target: Union[ast.List, ast.Tuple],
        rhs_namespace: Namespace,
        extra_deps: Set[Symbol],
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
            inner_deps |= extra_deps
            if isinstance(inner_target, (ast.List, ast.Tuple)):
                inner_namespace = flow().namespaces.get(inner_dep.obj_id, None)
                if inner_namespace is None or inner_namespace.obj is None:
                    self._handle_store_target_tuple_unpack_from_deps(
                        inner_target, inner_deps
                    )
                else:
                    self._handle_store_target_tuple_unpack_from_namespace(
                        inner_target, inner_namespace, extra_deps
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
            if rhs_namespace is None or rhs_namespace.obj is None:
                self._handle_store_target_tuple_unpack_from_deps(
                    target, resolve_rval_symbols(value)
                )
            else:
                extra_deps: Set[Symbol] = set()
                if isinstance(value, ast.Call):
                    # in this case, every target should depend on whatever was called
                    extra_deps |= resolve_rval_symbols(value)
                self._handle_store_target_tuple_unpack_from_namespace(
                    target, rhs_namespace, extra_deps
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
            if is_import:
                dep_node_as_alias = cast(ast.alias, dep_node)
                if isinstance(self.stmt_node, ast.ImportFrom):
                    module = sys.modules.get(
                        f"{self.stmt_node.module}.{dep_node_as_alias.name}"
                    ) or sys.modules.get(self.stmt_node.module)
                else:
                    module = sys.modules.get(dep_node_as_alias.name)
                if module not in (None, builtins):
                    module_sym = tracer().create_if_not_exists_module_symbol(
                        module,
                        self.stmt_node,
                        is_load=False,
                        is_named=dep_node_as_alias.asname is None,
                    )
                    if module_sym is not None:
                        rval_deps.update(flow().aliases.get(module_sym.obj_id, set()))
                target_as_str = cast(str, target)
                if target_as_str == "*" or "." in target_as_str:
                    continue
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
                    is_cascading_reactive=self.stmt_contains_cascading_reactive_rval,
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
                if flow().is_dev_mode:
                    logger.warning(
                        "keyerror for %s",
                        ast.dump(target) if isinstance(target, ast.AST) else target,
                    )
                # TODO: put this back in and debug
                # if flow().is_test:
                #     raise ke
            except ImportError:
                raise
            except Exception as e:
                logger.warning("exception while handling store: %s", e)
                if flow().is_test:
                    raise e

    def handle_dependencies(self) -> None:
        for external_call in tracer().external_calls:
            logger.info("external call: %s", external_call)
            external_call._handle_impl()
        if self._contains_lval():
            self._make_lval_data_symbols()
        elif isinstance(self.stmt_node, ast.Delete):
            self._handle_delete()
        else:
            # make sure usage timestamps get bumped
            resolve_rval_symbols(self.stmt_node)

    def mark_finished(self) -> None:
        self._finished = True
        # avoid keeping dangling references to stack frames once we're done with them
        self.frame = None

    def finished_execution_hook(self) -> None:
        if self._finished:
            return
        self.handle_dependencies()
        with tracer().dataflow_tracing_disabled():
            for sym in list(tracer().this_stmt_updated_symbols):
                passing_watchpoints = sym.watchpoints(
                    sym.obj,
                    position=(
                        flow().get_position(self.frame)[0],
                        self.lineno,
                    ),
                    symbol_name=sym.readable_name,
                )
                if passing_watchpoints:
                    flow().active_watchpoints.append((passing_watchpoints, sym))
        self.mark_finished()


if len(_StatementContainer) == 0:
    _StatementContainer.append(Statement)
else:
    _StatementContainer[0] = Statement
