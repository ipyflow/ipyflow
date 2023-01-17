# -*- coding: utf-8 -*-
import ast
import logging
import sys
from enum import Enum
from types import FrameType
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Generator,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    cast,
)

from pyccolo import fast

from ipyflow.analysis.slicing import compute_slice_impl, make_slice_text
from ipyflow.config import ExecutionMode, ExecutionSchedule, FlowDirection
from ipyflow.data_model import sizing
from ipyflow.data_model.annotation_utils import (
    get_type_annotation,
    make_annotation_string,
)
from ipyflow.data_model.code_cell import CodeCell, cells
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.data_model.update_protocol import UpdateProtocol
from ipyflow.singletons import flow, tracer
from ipyflow.tracing.watchpoint import Watchpoints
from ipyflow.types import IMMUTABLE_PRIMITIVE_TYPES, CellId, SupportedIndexType
from ipyflow.utils.misc_utils import cleanup_discard, debounce

if TYPE_CHECKING:
    # avoid circular imports
    from ipyflow.data_model.namespace import Namespace
    from ipyflow.data_model.scope import Scope

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


@debounce(0.1)
def _debounced_exec_schedule(executed_cell_id: CellId) -> None:
    flow().handle(
        {"type": "compute_exec_schedule", "executed_cell_id": executed_cell_id}
    )


class DataSymbolType(Enum):
    DEFAULT = "default"
    SUBSCRIPT = "subscript"
    FUNCTION = "function"
    CLASS = "class"
    IMPORT = "import"
    MODULE = "module"
    ANONYMOUS = "anonymous"


class DataSymbol:

    NULL = object()

    # object for virtual display symbol
    DISPLAY = object()

    IMMUTABLE_TYPES = set(IMMUTABLE_PRIMITIVE_TYPES)

    def __init__(
        self,
        name: SupportedIndexType,
        symbol_type: DataSymbolType,
        obj: Any,
        containing_scope: "Scope",
        stmt_node: Optional[ast.stmt] = None,
        symbol_node: Optional[ast.AST] = None,
        refresh_cached_obj: bool = False,
        implicit: bool = False,
    ) -> None:
        if refresh_cached_obj:
            # TODO: clean up redundancies
            assert implicit
            assert stmt_node is None
        self.name = name
        self.symbol_type = symbol_type
        self.obj = obj
        self._tombstone = False
        self._cached_out_of_sync = True
        self.cached_obj_id = None
        self.cached_obj_type = None
        if refresh_cached_obj:
            self._refresh_cached_obj()
        self.containing_scope = containing_scope
        self.call_scope: Optional[Scope] = None
        self.func_def_stmt: Optional[ast.stmt] = None
        self.stmt_node = self.update_stmt_node(stmt_node)
        self.symbol_node = symbol_node
        self._funcall_live_symbols = None
        self.parents: Dict["DataSymbol", List[Timestamp]] = {}
        self.children: Dict["DataSymbol", List[Timestamp]] = {}

        # initialize at -1 for implicit since the corresponding piece of data could already be around,
        # and we don't want liveness checker to think this was newly created unless we
        # explicitly trace an update somewhere
        self._timestamp: Timestamp = (
            Timestamp.uninitialized() if implicit else Timestamp.current()
        )
        # we need this to ensure we always use the latest version even for things like tuple unpack
        self._last_refreshed_timestamp = (
            Timestamp.uninitialized() if implicit else Timestamp.current()
        )
        # The version is a simple counter not associated with cells that is bumped whenever the timestamp is updated
        self._version: int = 0
        self._defined_cell_num = cells().exec_counter()
        self._cascading_reactive_cell_num = -1
        self._override_ready_liveness_cell_num = -1
        self._override_timestamp: Optional[Timestamp] = None
        self.watchpoints = Watchpoints()

        # The necessary last-updated timestamp / cell counter for this symbol to not be waiting
        self.required_timestamp: Timestamp = self.timestamp

        # for each usage of this dsym, the version that was used, if different from the timestamp of usage
        self.timestamp_by_used_time: Dict[Timestamp, Timestamp] = {}
        self.used_node_by_used_time: Dict[Timestamp, ast.AST] = {}
        # History of definitions at time of liveness
        self.timestamp_by_liveness_time: Dict[Timestamp, Timestamp] = {}
        # All timestamps associated with this symbol
        self.updated_timestamps: Set[Timestamp] = set()

        self.fresher_ancestors: Set["DataSymbol"] = set()
        self.fresher_ancestor_timestamps: Set[Timestamp] = set()

        # cells where this symbol was live
        self.cells_where_deep_live: Set[CodeCell] = set()
        self.cells_where_shallow_live: Set[CodeCell] = set()

        self._last_computed_ready_or_waiting_cache_ts: int = -1
        self._is_ready_or_waiting_at_position_cache: Dict[Tuple[int, bool], bool] = {}

        # if implicitly created when tracing non-store-context ast nodes
        self._implicit = implicit

        # Will never be stale if no_warning is True
        self.disable_warnings = False
        self._temp_disable_warnings = False

        flow().aliases.setdefault(id(obj), set()).add(self)
        if (
            isinstance(self.name, str)
            and not self.is_anonymous
            and not self.containing_scope.is_namespace_scope
        ):
            ns = self.namespace
            if ns is not None and ns.scope_name == "self":
                # hack to get a better name than `self.whatever` for fields of this object
                # not ideal because it relies on the `self` convention but is probably
                # acceptable for the use case of improving readable names
                ns.scope_name = self.name

    @property
    def aliases(self) -> List["DataSymbol"]:
        return list(flow().aliases.get(self.obj_id, []))

    @property
    def cells_where_live(self) -> Set[CodeCell]:
        return self.cells_where_deep_live | self.cells_where_shallow_live

    def __repr__(self) -> str:
        return f"<{self.readable_name}>"

    def __str__(self) -> str:
        return self.readable_name

    def __hash__(self) -> int:
        return hash(id(self))

    def temporary_disable_warnings(self) -> None:
        self._temp_disable_warnings = True

    @property
    def last_used_timestamp(self) -> Timestamp:
        if len(self.timestamp_by_used_time) == 0:
            return Timestamp.uninitialized()
        else:
            return max(self.timestamp_by_used_time.keys())

    @property
    def namespace_waiting_symbols(self) -> Set["DataSymbol"]:
        ns = self.namespace
        return set() if ns is None else ns.namespace_waiting_symbols

    @property
    def timestamp_excluding_ns_descendents(self) -> Timestamp:
        if self._override_timestamp is None:
            return self._timestamp
        else:
            return max(self._timestamp, self._override_timestamp)

    @property
    def timestamp(self) -> Timestamp:
        ts = self.timestamp_excluding_ns_descendents
        if self.is_import or self.is_module:
            return ts
        ns = self.namespace
        return ts if ns is None else max(ts, ns.max_descendent_timestamp)

    def compute_namespace_timestamps(
        self, seen: Optional[Set["DataSymbol"]] = None
    ) -> Set[Timestamp]:
        timestamps = {self.timestamp_excluding_ns_descendents, self.timestamp}
        ns = self.namespace
        if ns is None:
            return timestamps
        if seen is None:
            seen = set()
        if self in seen:
            return timestamps
        seen.add(self)
        for dsym in ns.all_data_symbols_this_indentation():
            timestamps |= dsym.compute_namespace_timestamps(seen=seen)
        return timestamps

    def code(self) -> str:
        ts = self.timestamp_excluding_ns_descendents
        if ts.cell_num == -1:
            timestamps = {Timestamp(self.defined_cell_num, ts.stmt_num)}
        else:
            timestamps = self.compute_namespace_timestamps()
        ts_deps = compute_slice_impl(list(timestamps), match_seed_stmts=True)
        stmts_by_cell_num = CodeCell.compute_slice_stmts_for_timestamps(ts_deps)
        stmt_text_by_cell_num = CodeCell.get_stmt_text(stmts_by_cell_num)
        return make_slice_text(stmt_text_by_cell_num, blacken=True)

    def cascading_reactive_cell_num(
        self,
        seen: Optional[Set["DataSymbol"]] = None,
        consider_containing_symbols: bool = True,
    ) -> int:
        if seen is None:
            seen = set()
        if self in seen:
            return -1
        seen.add(self)
        cell_num = self._cascading_reactive_cell_num
        ns = self.namespace
        ret = (
            cell_num
            if ns is None
            else max(
                cell_num,
                ns.max_cascading_reactive_cell_num(seen),
            )
        )
        if not consider_containing_symbols:
            return ret
        for sym in self.iter_containing_symbols():
            ret = max(ret, sym.cascading_reactive_cell_num(seen=seen))
        return ret

    def bump_cascading_reactive_cell_num(self, ctr: Optional[int] = None) -> None:
        self._cascading_reactive_cell_num = max(
            self._cascading_reactive_cell_num,
            flow().cell_counter() if ctr is None else ctr,
        )

    def iter_containing_symbols(self) -> Generator["DataSymbol", None, None]:
        yield self
        ns = self.containing_namespace
        if ns is None or not ns.is_namespace_scope:
            return
        for containing_ns in ns.iter_containing_namespaces():
            yield from flow().aliases.get(containing_ns.obj_id, [])

    @property
    def waiting_timestamp(self) -> int:
        return max(self._timestamp.cell_num, flow().min_timestamp)

    @property
    def defined_cell_num(self) -> int:
        return self._defined_cell_num

    @property
    def readable_name(self) -> str:
        return self.containing_scope.make_namespace_qualified_name(self)

    @property
    def is_subscript(self) -> bool:
        return self.symbol_type == DataSymbolType.SUBSCRIPT

    @property
    def is_class(self) -> bool:
        return self.symbol_type == DataSymbolType.CLASS

    @property
    def is_function(self) -> bool:
        return self.symbol_type == DataSymbolType.FUNCTION

    @property
    def is_import(self) -> bool:
        return self.symbol_type == DataSymbolType.IMPORT

    @property
    def is_module(self) -> bool:
        return self.symbol_type == DataSymbolType.MODULE

    @property
    def imported_module(self) -> str:
        if not self.is_import:
            raise ValueError("only IMPORT symbols have `imported_module` property")
        if isinstance(self.stmt_node, ast.Import):
            for alias in self.stmt_node.names:
                name = alias.asname or alias.name
                if name == self.name:
                    return alias.name
            raise ValueError(
                "Unable to find module for symbol %s is stmt %s"
                % (self, ast.dump(self.stmt_node))
            )
        elif isinstance(self.stmt_node, ast.ImportFrom):
            return self.stmt_node.module
        else:
            raise TypeError(
                "Invalid stmt type for import symbol: %s" % ast.dump(self.stmt_node)
            )

    @property
    def imported_symbol_original_name(self) -> str:
        if not self.is_import:
            raise ValueError(
                "only IMPORT symbols have `imported_symbol_original_name` property"
            )
        if isinstance(self.stmt_node, ast.Import):
            return self.imported_module
        elif isinstance(self.stmt_node, ast.ImportFrom):
            for alias in self.stmt_node.names:
                name = alias.asname or alias.name
                if name == self.name:
                    return alias.name
            raise ValueError(
                "Unable to find module for symbol %s is stmt %s"
                % (self, ast.dump(self.stmt_node))
            )
        else:
            raise TypeError(
                "Invalid stmt type for import symbol: %s" % ast.dump(self.stmt_node)
            )

    def is_cascading_reactive_at_counter(self, ctr: int) -> bool:
        return self.cascading_reactive_cell_num() > max(
            ctr, flow().min_cascading_reactive_cell_num
        )

    def get_top_level(self) -> Optional["DataSymbol"]:
        if not self.containing_scope.is_namespace_scope:
            return self
        else:
            containing_scope = cast("Namespace", self.containing_scope)
            for alias in flow().aliases.get(containing_scope.obj_id, []):
                if alias.is_globally_accessible:
                    return alias.get_top_level()
            return None

    def get_import_string(self) -> str:
        if not self.is_import:
            raise ValueError("only IMPORT symbols support recreating the import string")
        module = self.imported_module
        if isinstance(self.stmt_node, ast.Import):
            if module == self.name:
                return f"import {module}"
            else:
                return f"import {module} as {self.name}"
        elif isinstance(self.stmt_node, ast.ImportFrom):
            original_symbol_name = self.imported_symbol_original_name
            if original_symbol_name == self.name:
                return f"from {module} import {original_symbol_name}"
            else:
                return f"from {module} import {original_symbol_name} as {self.name}"
        else:
            raise TypeError(
                "Invalid stmt type for import symbol: %s" % ast.dump(self.stmt_node)
            )

    @property
    def is_anonymous(self) -> bool:
        if self.symbol_type == DataSymbolType.ANONYMOUS:
            return True
        ns = self.containing_namespace
        if ns is not None and ns.is_anonymous:
            return True
        return False

    @property
    def is_implicit(self) -> bool:
        return self._implicit

    def shallow_clone(
        self, new_obj: Any, new_containing_scope: "Scope", symbol_type: DataSymbolType
    ) -> "DataSymbol":
        return self.__class__(self.name, symbol_type, new_obj, new_containing_scope)

    @property
    def obj_id(self) -> int:
        return id(self.obj)

    @property
    def obj_type(self) -> Type[Any]:
        return type(self.obj)

    def get_type_annotation(self):
        return get_type_annotation(self.obj)

    def get_type_annotation_string(self) -> str:
        return make_annotation_string(self.get_type_annotation())

    @property
    def namespace(self) -> Optional["Namespace"]:
        return flow().namespaces.get(self.obj_id, None)

    @property
    def containing_namespace(self) -> Optional["Namespace"]:
        if self.containing_scope.is_namespace_scope:
            return cast("Namespace", self.containing_scope)
        else:
            return None

    @property
    def full_path(self) -> Tuple[str, ...]:
        return self.containing_scope.full_path + (str(self.name),)

    @property
    def full_namespace_path(self) -> str:
        return self.containing_scope.make_namespace_qualified_name(self)

    @property
    def is_garbage(self) -> bool:
        return self._tombstone

    def is_new_garbage(self) -> bool:
        if self._tombstone:
            return False
        containing_ns = self.containing_namespace
        numpy = sys.modules.get("numpy", None)
        if (
            numpy is not None
            and containing_ns is not None
            and isinstance(containing_ns.obj, numpy.ndarray)
        ):
            # numpy atoms are not interned (so assigning array elts to a variable does not bump refcount);
            # also seems that refcount is always 0, so just check if the containing namespace is garbage
            return self.containing_namespace.is_garbage
        return self.get_ref_count() == 0

    @property
    def is_globally_accessible(self) -> bool:
        return self.containing_scope.is_globally_accessible

    @property
    def is_user_accessible(self) -> bool:
        return (
            self.is_globally_accessible
            and not self.is_anonymous
            and not self.is_garbage
            and not (
                self.containing_namespace is not None
                and (
                    self.containing_namespace.is_anonymous
                    or self.containing_namespace.is_garbage
                )
            )
        )

    def _remove_self_from_aliases(self) -> None:
        cleanup_discard(flow().aliases, self.obj_id, self)
        self.obj = None

    def mark_garbage(self) -> None:
        if self.is_garbage:
            return
        self._tombstone = True
        ns = self.namespace
        if ns is not None and all(alias.is_garbage for alias in self.aliases):
            ns.mark_garbage()

    def collect_self_garbage(self) -> None:
        assert self.is_garbage
        flow().blocked_reactive_timestamps_by_symbol.pop(self, None)
        self._remove_self_from_aliases()
        for parent in self.parents:
            parent.children.pop(self, None)
        for child in self.children:
            child.parents.pop(self, None)
        containing_ns = self.containing_namespace
        if self.is_subscript and containing_ns is not None:
            containing_ns._subscript_data_symbol_by_name.pop(self.name, None)
        elif not self.is_subscript:
            self.containing_scope._data_symbol_by_name.pop(self.name, None)
        else:
            logger.warning(
                "could not find symbol %s in its scope %s", self, self.containing_scope
            )
        self.containing_scope = None

    # def update_type(self, new_type):
    #     self.symbol_type = new_type
    #     if self.is_function:
    #         self.call_scope = self.containing_scope.make_child_scope(self.name)
    #     else:
    #         self.call_scope = None

    def update_obj_ref(self, obj: Any, refresh_cached: bool = True) -> None:
        self._tombstone = False
        self._cached_out_of_sync = True
        if (
            flow().settings.mark_typecheck_failures_unsafe
            and self.cached_obj_type != type(obj)
        ):
            for cell in self.cells_where_live:
                cell.invalidate_typecheck_result()
        self.cells_where_shallow_live.clear()
        self.cells_where_deep_live.clear()
        self.obj = obj
        if self.cached_obj_id is not None and self.cached_obj_id != self.obj_id:
            new_ns = flow().namespaces.get(self.obj_id, None)
            # don't overwrite existing namespace for this obj
            old_ns = flow().namespaces.get(self.cached_obj_id, None)
            if (
                old_ns is not None
                and old_ns.full_namespace_path == self.full_namespace_path
            ):
                if new_ns is None:
                    logger.info("create fresh copy of namespace %s", old_ns)
                    new_ns = old_ns.fresh_copy(obj)
                    old_ns.transfer_symbols_to(new_ns)
                else:
                    new_ns.scope_name = old_ns.scope_name
                    new_ns.parent_scope = old_ns.parent_scope
            self._handle_aliases()
            if (
                old_ns is not None
                and len(flow().aliases.get(self.cached_obj_id, [])) == 0
            ):
                old_ns.mark_garbage()
        if refresh_cached:
            self._refresh_cached_obj()

    def invalidate_cached(self) -> None:
        self._cached_out_of_sync = True
        self.cached_obj_id = None
        self.cached_obj_type = None

    def get_ref_count(self) -> int:
        if self.obj is None or self.obj is DataSymbol.NULL:
            return -1
        total = sys.getrefcount(self.obj) - 1
        total -= len(flow().aliases.get(self.obj_id, []))
        ns = flow().namespaces.get(self.obj_id, None)
        if ns is not None and ns.obj is not None and ns.obj is not DataSymbol.NULL:
            total -= 1
        return total

    def should_preserve_timestamp(self, prev_obj: Optional[Any]) -> bool:
        if flow().mut_settings.exec_mode == ExecutionMode.REACTIVE:
            # always bump timestamps for reactive mode
            return False
        if flow().mut_settings.exec_schedule in (
            ExecutionSchedule.DAG_BASED,
            ExecutionSchedule.HYBRID_DAG_LIVENESS_BASED,
        ):
            # always bump timestamps for dag schedule
            return False
        if prev_obj is None:
            return False
        if (
            flow().blocked_reactive_timestamps_by_symbol.get(self, -1)
            == self.timestamp.cell_num
        ):
            return False
        if not self._cached_out_of_sync or self.obj_id == self.cached_obj_id:
            return True
        if self.obj is None or prev_obj is DataSymbol.NULL:
            return self.obj is None and prev_obj is DataSymbol.NULL
        obj_type = type(self.obj)
        prev_type = type(prev_obj)
        if obj_type != prev_type:
            return False
        obj_size_ubound = sizing.sizeof(self.obj)
        if obj_size_ubound > sizing.MAX_SIZE:
            return False
        cached_obj_size_ubound = sizing.sizeof(prev_obj)
        if cached_obj_size_ubound > sizing.MAX_SIZE:
            return False
        return (obj_size_ubound == cached_obj_size_ubound) and self.obj == prev_obj

    def _handle_aliases(self):
        cleanup_discard(flow().aliases, self.cached_obj_id, self)
        flow().aliases.setdefault(self.obj_id, set()).add(self)

    def update_stmt_node(self, stmt_node: Optional[ast.stmt]) -> Optional[ast.stmt]:
        self.stmt_node = stmt_node
        self._funcall_live_symbols = None
        if self.is_function or (
            stmt_node is not None and isinstance(stmt_node, ast.Lambda)
        ):
            # TODO: in the case of lambdas, there will not necessarily be one
            #  symbol for a given statement. We need a more precise way to determine
            #  the symbol being called than by looking at the stmt in question.
            flow().statement_to_func_cell[id(stmt_node)] = self
            self.call_scope = self.containing_scope.make_child_scope(self.name)
            self.func_def_stmt = stmt_node
        return stmt_node

    def _refresh_cached_obj(self) -> None:
        self._cached_out_of_sync = False
        # don't keep an actual ref to avoid bumping prefcount
        self.cached_obj_id = self.obj_id
        self.cached_obj_type = self.obj_type

    def get_definition_args(self) -> List[ast.arg]:
        assert self.func_def_stmt is not None and isinstance(
            self.func_def_stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        )
        args = []
        for arg in self.func_def_stmt.args.args + self.func_def_stmt.args.kwonlyargs:
            args.append(arg)
        if self.func_def_stmt.args.vararg is not None:
            args.append(self.func_def_stmt.args.vararg)
        if self.func_def_stmt.args.kwarg is not None:
            args.append(self.func_def_stmt.args.kwarg)
        return args

    def _match_call_args_with_definition_args(
        self,
    ) -> Generator[Tuple[ast.arg, List["DataSymbol"]], None, None]:
        # TODO: handle posonlyargs, kwonlyargs
        assert self.func_def_stmt is not None and isinstance(
            self.func_def_stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        )
        caller_node = self._get_calling_ast_node()
        if caller_node is None or not isinstance(caller_node, ast.Call):
            return
        kwarg_by_name = {
            arg_key.arg: arg_key
            for arg_key in self.func_def_stmt.args.args[
                -len(self.func_def_stmt.args.defaults) :
            ]
        }
        if not all(keyword.arg in kwarg_by_name for keyword in caller_node.keywords):
            logger.warning("detected mismatched kwargs from caller node to definition")
            return
        def_args = self.func_def_stmt.args.args
        if len(self.func_def_stmt.args.defaults) > 0:
            def_args = def_args[: -len(self.func_def_stmt.args.defaults)]
        if len(def_args) > 0 and def_args[0].arg == "self":
            # FIXME: this is bad and I should feel bad
            def_args = def_args[1:]
        for def_arg, call_arg in zip(def_args, caller_node.args):
            if isinstance(call_arg, ast.Starred):
                # give up
                # TODO: handle this case
                break
            yield def_arg, tracer().resolve_loaded_symbols(call_arg)
        seen_keys = set()
        for keyword in caller_node.keywords:
            keyword_key, keyword_value = keyword.arg, keyword.value
            if keyword_value is None:
                continue
            seen_keys.add(keyword_key)
            yield kwarg_by_name[keyword_key], tracer().resolve_loaded_symbols(
                keyword_value
            )
        for arg_key, arg_value in zip(
            self.func_def_stmt.args.args[-len(self.func_def_stmt.args.defaults) :],
            self.func_def_stmt.args.defaults,
        ):
            if arg_key.arg in seen_keys:
                continue
            yield arg_key, tracer().resolve_loaded_symbols(arg_value)

    def _get_calling_ast_node(self) -> Optional[ast.Call]:
        if tracer().tracing_disabled_since_last_module_stmt or (
            not hasattr(self.obj, "__module__")
            and getattr(type(self.obj), "__module__", None) == "builtins"
        ):
            return None
        if self.func_def_stmt is not None and isinstance(
            self.func_def_stmt, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            if self.name in ("__getitem__", "__setitem__", "__delitem__"):
                # TODO: handle case where we're looking for a subscript for the calling node
                return None
            for decorator in self.func_def_stmt.decorator_list:
                if isinstance(decorator, ast.Name) and decorator.id == "property":
                    # TODO: handle case where we're looking for an attribute for the calling node
                    return None
        lexical_call_stack = tracer().lexical_call_stack
        if len(lexical_call_stack) == 0:
            return None
        prev_node_id_in_cur_frame_lexical = lexical_call_stack.get_field(
            "prev_node_id_in_cur_frame_lexical"
        )
        caller_ast_node = tracer().ast_node_by_id.get(
            prev_node_id_in_cur_frame_lexical, None
        )
        if caller_ast_node is None or not isinstance(caller_ast_node, ast.Call):
            return None
        return caller_ast_node

    def create_symbols_for_call_args(self, call_frame: FrameType) -> None:
        assert self.func_def_stmt is not None
        seen_def_args = set()
        logger.info("create symbols for call to %s", self)
        for def_arg, deps in self._match_call_args_with_definition_args():
            seen_def_args.add(def_arg.arg)
            sym = self.call_scope.upsert_data_symbol_for_name(
                def_arg.arg,
                call_frame.f_locals.get(def_arg.arg),
                deps,
                self.func_def_stmt,
                propagate=False,
                symbol_node=def_arg,
            )
            logger.info("def arg %s matched with deps %s", def_arg, deps)
        for def_arg in self.get_definition_args():
            if def_arg.arg in seen_def_args:
                continue
            self.call_scope.upsert_data_symbol_for_name(
                def_arg.arg,
                None,
                set(),
                self.func_def_stmt,
                propagate=False,
                symbol_node=def_arg,
            )

    @property
    def is_waiting(self) -> bool:
        if self.disable_warnings or self._temp_disable_warnings:
            return False
        if self.waiting_timestamp < self.required_timestamp.cell_num:
            return True
        elif flow().min_timestamp == -1:
            return len(self.namespace_waiting_symbols) > 0
        else:
            # TODO: guard against infinite recurision
            return any(sym.is_waiting for sym in self.namespace_waiting_symbols)

    @property
    def is_shallow_stale(self) -> bool:
        if self.disable_warnings or self._temp_disable_warnings:
            return False
        return self.waiting_timestamp < self.required_timestamp.cell_num

    def _is_ready_or_waiting_at_position_impl(self, pos: int, deep: bool) -> bool:
        for par, timestamps in self.parents.items():
            for ts in timestamps:
                dep_introduced_pos = cells().from_timestamp(ts).position
                if dep_introduced_pos > pos:
                    continue
                for updated_ts in par.updated_timestamps:
                    if cells().from_timestamp(updated_ts).position > dep_introduced_pos:
                        continue
                    if updated_ts.cell_num > ts.cell_num or par.is_waiting_at_position(
                        dep_introduced_pos
                    ):
                        # logger.error("sym: %s", self)
                        # logger.error("pos: %s", pos)
                        # logger.error("parent: %s", par)
                        # logger.error("dep introdced ts: %s", ts)
                        # logger.error("dep introdced pos: %s", dep_introduced_pos)
                        # logger.error("par updated ts: %s", updated_ts)
                        # logger.error("par updated position: %s", cells().from_timestamp(updated_ts).position)
                        return True
        if deep:
            for sym in self.namespace_waiting_symbols:
                if sym.is_waiting_at_position(pos):
                    return True
        return False

    def is_waiting_at_position(self, pos: int, deep: bool = True) -> bool:
        if deep:
            if not self.is_waiting:
                return False
        else:
            if not self.is_shallow_stale:
                return False
        if flow().mut_settings.flow_order == FlowDirection.ANY_ORDER:
            return True
        if cells().exec_counter() > self._last_computed_ready_or_waiting_cache_ts:
            self._is_ready_or_waiting_at_position_cache.clear()
            self._last_computed_ready_or_waiting_cache_ts = cells().exec_counter()
        if (pos, deep) in self._is_ready_or_waiting_at_position_cache:
            return self._is_ready_or_waiting_at_position_cache[pos, deep]
        # preemptively set this entry to 'False' in the cache to avoid infinite loops
        self._is_ready_or_waiting_at_position_cache[pos, deep] = False
        is_waiting = self._is_ready_or_waiting_at_position_impl(pos, deep)
        self._is_ready_or_waiting_at_position_cache[pos, deep] = is_waiting
        return is_waiting

    def should_mark_waiting(self, updated_dep):
        if self.disable_warnings:
            return False
        if updated_dep is self:
            return False
        return True

    def _is_underscore_or_simple_assign(self, new_deps: Set["DataSymbol"]) -> bool:
        if self.name == "_":
            # FIXME: distinguish between explicit assignment to _ from user and implicit assignment from kernel
            return True
        if not isinstance(self.stmt_node, (ast.Assign, ast.AnnAssign)):
            return False
        if len(new_deps) != 1:
            return False
        only_dep: DataSymbol = next(iter(new_deps))
        # obj ids can get reused for anon symbols like literals
        return not only_dep.is_anonymous and self.cached_obj_id == only_dep.obj_id

    def update_deps(
        self,
        new_deps: Set["DataSymbol"],
        prev_obj: Any = None,
        overwrite: bool = True,
        mutated: bool = False,
        deleted: bool = False,
        propagate_to_namespace_descendents: bool = False,
        propagate: bool = True,
        refresh: bool = True,
        is_cascading_reactive: Optional[bool] = None,
    ) -> None:
        if self.is_import and self.obj_id == self.cached_obj_id:
            # skip updates for imported symbols
            # just bump the version if it's newly created
            if mutated or not self._timestamp.is_initialized:
                self._timestamp = Timestamp.current()
            return
        if overwrite and not self.is_globally_accessible:
            self.watchpoints.clear()
        if mutated and self.obj_type in self.IMMUTABLE_TYPES:
            return
        # if we get here, no longer implicit
        self._implicit = False
        # quick last fix to avoid overwriting if we appear inside the set of deps to add (or a 1st order ancestor)
        # TODO: check higher-order ancestors too?
        overwrite = overwrite and self not in new_deps
        overwrite = overwrite and not any(
            self in new_dep.parents for new_dep in new_deps
        )
        logger.warning("symbol %s new deps %s", self, new_deps)
        new_deps.discard(self)
        if overwrite:
            for parent in self.parents.keys() - new_deps:
                parent.children.pop(self, None)
                self.parents.pop(parent, None)

        for new_parent in new_deps - self.parents.keys():
            if new_parent is None:
                continue
            new_parent.children.setdefault(self, []).append(Timestamp.current())
            self.parents.setdefault(new_parent, []).append(Timestamp.current())
        self.required_timestamp = Timestamp.uninitialized()
        self.fresher_ancestors.clear()
        self.fresher_ancestor_timestamps.clear()
        if mutated or isinstance(self.stmt_node, ast.AugAssign):
            self.update_usage_info()
        should_preserve_timestamp = not mutated and self.should_preserve_timestamp(
            prev_obj
        )
        prev_cell = cells().current_cell().prev_cell
        prev_cell_ctr = -1 if prev_cell is None else prev_cell.cell_ctr
        if overwrite:
            self._cascading_reactive_cell_num = -1
            flow().updated_reactive_symbols.discard(self)
            flow().updated_deep_reactive_symbols.discard(self)
        if is_cascading_reactive is not None:
            is_cascading_reactive = is_cascading_reactive or any(
                dsym.is_cascading_reactive_at_counter(prev_cell_ctr)
                for dsym in new_deps
            )
        if is_cascading_reactive:
            bump_version = refresh
            self.bump_cascading_reactive_cell_num()
        elif self.cascading_reactive_cell_num() == flow().cell_counter():
            bump_version = refresh
        else:
            bump_version = refresh and (
                not should_preserve_timestamp
                or type(self.obj) not in DataSymbol.IMMUTABLE_TYPES
            )
        if refresh:
            self.refresh(
                bump_version=bump_version,
                # rationale: if this is a mutation for which we have more precise information,
                # then we don't need to update the ns descendents as this will already have happened.
                # also don't update ns descendents for things like `a = b`
                refresh_descendent_namespaces=not (
                    mutated and not propagate_to_namespace_descendents
                )
                and not self._is_underscore_or_simple_assign(new_deps),
                refresh_namespace_waiting=not mutated,
            )
        if propagate and (deleted or not should_preserve_timestamp):
            UpdateProtocol(self)(
                new_deps, mutated, propagate_to_namespace_descendents, refresh
            )
        self._refresh_cached_obj()
        if self.is_class:
            # pop pending class defs and update obj ref
            pending_class_ns = tracer().pending_class_namespaces.pop()
            pending_class_ns.update_obj_ref(self.obj)
        for dep in new_deps:
            if dep.obj is self.obj and dep.call_scope is not None:
                self.call_scope = dep.call_scope
                self.func_def_stmt = dep.func_def_stmt
        ns = self.namespace
        if ns is not None and ns.scope_name == "self" and isinstance(self.name, str):
            # fixup namespace name if necessary
            # can happen if symbol for 'self' was created in a previous __init__
            ns.scope_name = self.name
        if overwrite and len(flow().aliases[self.obj_id]) == 1:
            self._handle_possible_widget_creation()

    def _handle_possible_widget_creation(self) -> None:
        if self.obj is None:
            return
        Widget = getattr(sys.modules.get("ipywidgets"), "Widget", None)
        if (
            Widget is None
            or not isinstance(self.obj, Widget)
            or not hasattr(self.obj, "observe")
            or not hasattr(self.obj, "value")
        ):
            return
        self.namespaced().upsert_data_symbol_for_name(
            "value", None, set(), self.stmt_node
        )
        self.obj.observe(self._observe_widget)

    def _observe_widget(self, msg: Dict[str, Any]) -> None:
        if msg.get("name") != "value" or "new" not in msg:
            return
        ns = self.namespace
        sym = ns.lookup_data_symbol_by_name_this_indentation("value")
        if sym is None:
            return
        newval = msg["new"]
        current_ts_cell = cells().from_timestamp(self._timestamp)
        current_ts_cell._extra_stmt = ast.parse(f"{sym.readable_name} = {newval}").body[
            0
        ]
        sym._override_ready_liveness_cell_num = flow().cell_counter() + 1
        sym._override_timestamp = Timestamp(
            self._timestamp.cell_num, self._timestamp.stmt_num + 1
        )
        flow().add_dynamic_data_dep(sym._timestamp, sym._override_timestamp, sym)
        flow().add_dynamic_data_dep(sym._override_timestamp, sym._timestamp, sym)
        _debounced_exec_schedule(cells().from_timestamp(self.timestamp).cell_id)

    def namespaced(self) -> "Namespace":
        ns = self.namespace
        if ns is not None:
            return ns
        # FIXME: workaround for circular dep
        Namespace = getattr(sys.modules["ipyflow.data_model.namespace"], "Namespace")
        return Namespace(self.obj, self.name, parent_scope=self.containing_scope)

    def update_usage_info(
        self,
        used_time: Optional[Timestamp] = None,
        used_node: Optional[ast.AST] = None,
        exclude_ns: bool = False,
        seen: Optional[Set["DataSymbol"]] = None,
        is_static: bool = False,
        is_blocking: bool = False,
    ) -> None:
        is_blocking = is_blocking or id(used_node) in tracer().blocking_node_ids
        if used_time is None:
            used_time = Timestamp.current()
        if flow().is_dev_mode:
            logger.info(
                "sym `%s` used in cell %d last updated in cell %d",
                self,
                used_time.cell_num,
                self.timestamp,
            )
        ts_to_use = self._timestamp  # if exclude_ns else self.timestamp
        if ts_to_use.is_initialized:
            ts_to_use = max(ts_to_use, self._last_refreshed_timestamp)
        timestamp_by_used_time = (
            self.timestamp_by_liveness_time
            if is_static
            else self.timestamp_by_used_time
        )
        if (
            ts_to_use.is_initialized
            and used_time not in timestamp_by_used_time
            and ts_to_use < used_time
        ):
            timestamp_by_used_time[used_time] = ts_to_use
            if not is_blocking:
                if is_static:
                    flow().add_static_data_dep(used_time, ts_to_use, self)
                else:
                    flow().add_dynamic_data_dep(used_time, ts_to_use, self)
            if used_node is not None:
                self.used_node_by_used_time[used_time] = used_node
        ns = None if exclude_ns else self.namespace
        if ns is not None and seen is None:
            seen = set()
        if ns is None or self in seen:
            return
        seen.add(self)
        for dsym in ns.all_data_symbols_this_indentation():
            dsym.update_usage_info(
                used_time=used_time,
                used_node=None,
                exclude_ns=False,
                seen=seen,
                is_static=is_static,
                is_blocking=is_blocking,
            )

    def refresh(
        self,
        bump_version: bool = True,
        refresh_descendent_namespaces: bool = False,
        refresh_namespace_waiting: bool = True,
        timestamp: Optional[Timestamp] = None,
        seen: Optional[Set["DataSymbol"]] = None,
    ) -> None:
        self._last_refreshed_timestamp = Timestamp.current()
        self._temp_disable_warnings = False
        if bump_version:
            self._timestamp = Timestamp.current() if timestamp is None else timestamp
            self._override_timestamp = None
            for cell in self.cells_where_live:
                cell.add_used_cell_counter(self, self._timestamp.cell_num)
            ns = self.containing_namespace
            if ns is not None:
                # logger.error("bump version of %s due to %s (value %s)", ns.full_path, self.full_path, self.obj)
                ns.max_descendent_timestamp = self.timestamp_excluding_ns_descendents
                for alias in flow().aliases.get(ns.obj_id, []):
                    for cell in alias.cells_where_deep_live:
                        cell.add_used_cell_counter(alias, self._timestamp.cell_num)
            self.updated_timestamps.add(self._timestamp)
            self._version += 1
        if refresh_descendent_namespaces:
            if seen is None:
                seen = set()
            if self in seen:
                return
            seen.add(self)
            ns = self.namespace
            if ns is not None:
                for dsym in ns.all_data_symbols_this_indentation(exclude_class=True):
                    # this is to handle cases like `x = x.mutate(42)`, where
                    # we could have changed some member of x but returned the
                    # original object -- in this case, just assume that all
                    # the stale namespace descendents are no longer stale, as
                    # this is likely the user intention. For an example, see
                    # `test_external_object_update_propagates_to_stale_namespace_symbols()`
                    # in `test_frontend_checker.py`
                    if not dsym.is_waiting or refresh_namespace_waiting:
                        # logger.error(
                        #     "refresh %s due to %s (value %s) via namespace %s",
                        #     dsym.full_path,
                        #     self.full_path,
                        #     self.obj,
                        #     ns.full_path,
                        # )
                        dsym.refresh(
                            refresh_descendent_namespaces=True,
                            timestamp=self.timestamp_excluding_ns_descendents,
                            seen=seen,
                        )
            if refresh_namespace_waiting:
                self.namespace_waiting_symbols.clear()
