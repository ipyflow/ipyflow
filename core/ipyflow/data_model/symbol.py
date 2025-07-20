# -*- coding: utf-8 -*-
import ast
import logging
import sys
from enum import Enum
from types import FrameType, FunctionType
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Generator,
    Iterable,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
    cast,
)

from ipyflow.config import ExecutionSchedule, FlowDirection
from ipyflow.data_model.cell import Cell, cells
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.data_model.utils.annotation_utils import (
    get_type_annotation,
    make_annotation_string,
)
from ipyflow.data_model.utils.update_protocol import UpdateProtocol
from ipyflow.models import _SymbolContainer, namespaces, statements, symbols
from ipyflow.singletons import flow, shell, tracer
from ipyflow.slicing.context import dynamic_slicing_context, slicing_context
from ipyflow.slicing.mixin import FormatType, Slice
from ipyflow.tracing.watchpoint import Watchpoints
from ipyflow.types import IMMUTABLE_PRIMITIVE_TYPES, IdType, SupportedIndexType
from ipyflow.utils.misc_utils import cleanup_discard, debounce

try:
    from importlib.util import _LazyModule  # type: ignore
except Exception:
    _LazyModule = None

if TYPE_CHECKING:
    import astunparse
elif hasattr(ast, "unparse"):
    astunparse = ast
else:
    import astunparse

if TYPE_CHECKING:
    # avoid circular imports
    from ipyflow.data_model.namespace import Namespace
    from ipyflow.data_model.scope import Scope

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


# just want to get rid of unused warning
_override_unused_warning_symbols = symbols


@debounce(0.1)
def _debounced_exec_schedule(executed_cell_id: IdType, reactive: bool) -> None:
    flow_ = flow()
    settings = flow_.mut_settings
    exec_schedule = settings.exec_schedule
    try:
        if exec_schedule == ExecutionSchedule.DAG_BASED:
            settings.exec_schedule = ExecutionSchedule.HYBRID_DAG_LIVENESS_BASED
        flow_.get_and_set_exception_raised_during_execution(None)
        flow_.comm_manager.handle(
            {
                "type": "compute_exec_schedule",
                "executed_cell_id": executed_cell_id,
                "is_reactively_executing": reactive,
                "allow_new_ready": reactive,
            }
        )
    finally:
        settings.exec_schedule = exec_schedule
        flow_.comm_manager.debounced_exec_schedule_pending = False


class SymbolType(Enum):
    DEFAULT = "default"
    SUBSCRIPT = "subscript"
    FUNCTION = "function"
    CLASS = "class"
    IMPORT = "import"
    MODULE = "module"
    ANONYMOUS = "anonymous"


class Symbol:
    NULL = object()

    # object for virtual display symbol
    DISPLAY = object()

    IMMUTABLE_TYPES = set(IMMUTABLE_PRIMITIVE_TYPES)

    IPYFLOW_MUTATION_VIRTUAL_SYMBOL_NAME = "__ipyflow_mutation"
    IPYFLOW_ITER_VIRTUAL_SYMBOL_NAME = "__ipyflow_iter"

    def __init__(
        self,
        name: SupportedIndexType,
        symbol_type: SymbolType,
        obj: Any,
        containing_scope: "Scope",
        stmt_node: Optional[Union[ast.stmt, ast.Lambda]] = None,
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

        # additional user-specific metadata
        self._tags: Set[str] = set()
        self.extra_metadata: Dict[str, Any] = {}

        self._tombstone = False
        self._cached_out_of_sync = True
        self.cached_obj_id: Optional[int] = None
        self.cached_obj_type: Optional[Type[object]] = None
        self.cached_obj_len: Optional[int] = None
        if refresh_cached_obj:
            self._refresh_cached_obj()
        self.containing_scope = containing_scope or flow().global_scope
        self.call_scope: Optional[Scope] = None
        self.func_def_stmt: Optional[Union[ast.stmt, ast.Lambda]] = None
        self.stmt_node = self.update_stmt_node(stmt_node)
        self.symbol_node = symbol_node
        self._funcall_live_symbols = None
        self.parents: Dict["Symbol", List[Timestamp]] = {}
        self.children: Dict["Symbol", List[Timestamp]] = {}

        # initialize at -1 for implicit since the corresponding piece of data could already be around,
        # and we don't want liveness checker to think this was newly created unless we
        # explicitly trace an update somewhere
        self._timestamp: Timestamp = (
            Timestamp.uninitialized() if implicit else Timestamp.current()
        )
        self._snapshot_timestamps: List[Timestamp] = []
        self._snapshot_timestamp_ubounds: List[Timestamp] = []
        self._defined_cell_num = cells().exec_counter()
        self._is_dangling_on_edges = False
        self._cascading_reactive_cell_num = -1
        self._override_ready_liveness_cell_num = -1
        self._override_timestamp: Optional[Timestamp] = None
        self.watchpoints = Watchpoints()

        # The necessary last-updated timestamp / cell counter for this symbol to not be waiting
        self.required_timestamp: Timestamp = self.timestamp

        # for each usage of this sym, the version that was used, if different from the timestamp of usage
        self.timestamp_by_used_time: Dict[Timestamp, Timestamp] = {}
        self.used_node_by_used_time: Dict[Timestamp, ast.AST] = {}
        # History of definitions at time of liveness
        self.timestamp_by_liveness_time: Dict[Timestamp, Timestamp] = {}
        # All timestamps associated with updates to this symbol
        self._updated_timestamps: Set[Timestamp] = set()
        # The most recent timestamp associated with a particular object id
        self.last_updated_timestamp_by_obj_id: Dict[int, Timestamp] = {}

        self.fresher_ancestors: Set["Symbol"] = set()
        self.fresher_ancestor_timestamps: Set[Timestamp] = set()

        # cells where this symbol was live
        self.cells_where_deep_live: Set[Cell] = set()
        self.cells_where_shallow_live: Set[Cell] = set()

        self._last_computed_ready_or_waiting_cache_ts: int = -1
        self._is_ready_or_waiting_at_position_cache: Dict[Tuple[int, bool], bool] = {}

        # if implicitly created when tracing non-store-context ast nodes
        self._implicit = implicit

        # Will never be stale if no_warning is True
        self.disable_warnings = False
        self._temp_disable_warnings = False

        self._num_ipywidget_observers = 0
        self._num_mercury_widget_observers = 0

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
        self._maybe_fix_implicitness()

    def _maybe_fix_implicitness(self) -> None:
        if (
            self.is_implicit
            and not self._timestamp.is_initialized
            and self.containing_namespace is not None
        ):
            self._timestamp = max(
                (
                    sym.timestamp
                    for sym in flow().aliases.get(self.containing_namespace.obj_id, [])
                ),
                default=self._timestamp,
            )
        if self._timestamp.is_initialized and self._implicit:
            self._implicit = False

    @property
    def updated_timestamps(self) -> Set[Timestamp]:
        init_ts = self._initialized_timestamp
        if init_ts.is_initialized:
            return self._updated_timestamps | {init_ts}
        else:
            return self._updated_timestamps

    @property
    def aliases(self) -> List["Symbol"]:
        return list(flow().aliases.get(self.obj_id, []))

    @property
    def cells_where_live(self) -> Set[Cell]:
        return self.cells_where_deep_live | self.cells_where_shallow_live

    def __repr__(self) -> str:
        return f"<{self.readable_name}>"

    def __str__(self) -> str:
        return self.readable_name

    def __hash__(self) -> int:
        return hash(id(self))

    def __lt__(self, other) -> bool:
        return id(self) < id(other)

    def add_tag(self, tag_value: str) -> None:
        self._tags.add(tag_value)

    def remove_tag(self, tag_value: str) -> None:
        self._tags.discard(tag_value)

    def has_tag(self, tag_value: str) -> bool:
        return tag_value in self._tags

    def temporary_disable_warnings(self) -> None:
        self._temp_disable_warnings = True

    @property
    def last_used_timestamp(self) -> Timestamp:
        if len(self.timestamp_by_used_time) == 0:
            return Timestamp.uninitialized()
        else:
            return max(self.timestamp_by_used_time.keys())

    @property
    def namespace_waiting_symbols(self) -> Set["Symbol"]:
        ns = self.namespace
        return set() if ns is None else ns.namespace_waiting_symbols

    @property
    def _initialized_timestamp(self) -> Timestamp:
        return self._get_initialized_timestamp()

    def _get_initialized_timestamp(
        self, seen: Optional[Set["Symbol"]] = None
    ) -> Timestamp:
        ts = self._timestamp
        if ts.is_initialized:
            return ts
        seen = seen or set()
        seen.add(self)
        ns = self.containing_namespace
        if ns is not None:
            for sym in flow().aliases.get(ns.obj_id, []):
                if sym in seen:
                    continue
                return sym._get_initialized_timestamp(seen=seen)
        return ts

    @property
    def shallow_timestamp(self) -> Timestamp:
        ts = self._initialized_timestamp
        if self._override_timestamp is None:
            return ts
        else:
            return max(ts, self._override_timestamp)

    @property
    def visible_timestamp(self) -> Optional[Timestamp]:
        for ts in sorted(self.updated_timestamps, reverse=True):
            if cells().at_timestamp(ts).is_visible:
                return ts
        return None

    @property
    def memoize_timestamp(self) -> Optional[Timestamp]:
        return self.last_updated_timestamp_by_obj_id.get(self.obj_id)

    @property
    def timestamp(self) -> Timestamp:
        ts = self.shallow_timestamp
        if self.is_import or self.is_module:
            return ts
        ns = self.namespace
        return ts if ns is None else max(ts, ns.max_descendent_timestamp)

    def _compute_namespace_timestamps(
        self,
        seen: Optional[Set["Symbol"]] = None,
        version_ubound: Optional[Timestamp] = None,
    ) -> Set[Timestamp]:
        if version_ubound is None:
            timestamps = {self.shallow_timestamp, self.timestamp}
        else:
            max_leq_ubound = Timestamp.uninitialized()
            for ts in reversed(self._snapshot_timestamps):
                if ts <= version_ubound:
                    max_leq_ubound = ts
                    break
            if max_leq_ubound.is_initialized:
                timestamps = {max_leq_ubound}
            else:
                timestamps = set()
        ns = self.namespace
        if ns is None:
            return timestamps
        if seen is None:
            seen = set()
        if self in seen:
            return timestamps
        seen.add(self)
        for sym in ns.all_symbols_this_indentation():
            timestamps |= sym._compute_namespace_timestamps(
                seen=seen, version_ubound=version_ubound
            )
        return timestamps

    def _get_timestamps_for_version(self, version: int) -> Set[Timestamp]:
        if len(self._snapshot_timestamps) == 0:
            return {self.timestamp}
        ts = self._snapshot_timestamps[version]
        if ts.cell_num == -1:
            return {Timestamp(self.defined_cell_num, ts.stmt_num)}
        else:
            return self._compute_namespace_timestamps(
                version_ubound=None if version == -1 else ts
            ) - {Timestamp.uninitialized()}

    def code(
        self, format_type: Optional[Type[FormatType]] = None, version: int = -1
    ) -> Slice:
        return statements().format_multi_slice(
            self._get_timestamps_for_version(version=version),
            blacken=True,
            format_type=format_type,
        )

    def cascading_reactive_cell_num(
        self,
        seen: Optional[Set["Symbol"]] = None,
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

    def iter_containing_symbols(self) -> Generator["Symbol", None, None]:
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
        return self.symbol_type == SymbolType.SUBSCRIPT

    @property
    def is_class(self) -> bool:
        return self.symbol_type == SymbolType.CLASS

    @property
    def is_function(self) -> bool:
        return self.symbol_type == SymbolType.FUNCTION

    @property
    def is_lambda(self) -> bool:
        # TODO: this is terrible
        return type(self.name) is str and self.name.startswith(  # noqa: E721
            "<lambda_sym_"
        )

    @property
    def is_import(self) -> bool:
        return self.symbol_type == SymbolType.IMPORT

    @property
    def is_module(self) -> bool:
        return self.symbol_type == SymbolType.MODULE

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
            assert (
                self.stmt_node.module is not None
            )  # in a repl, there shouldn't be relative imports
            return self.stmt_node.module
        else:
            raise TypeError(
                "Invalid stmt type for import symbol: %s" % None
                if self.stmt_node is None
                else ast.dump(self.stmt_node)
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
                "Invalid stmt type for import symbol: %s" % None
                if self.stmt_node is None
                else ast.dump(self.stmt_node)
            )

    def is_cascading_reactive_at_counter(self, ctr: int) -> bool:
        return self.cascading_reactive_cell_num() > max(
            ctr, flow().min_cascading_reactive_cell_num
        )

    def get_top_level(self) -> Optional["Symbol"]:
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
                "Invalid stmt type for import symbol: %s" % None
                if self.stmt_node is None
                else ast.dump(self.stmt_node)
            )

    @property
    def is_anonymous(self) -> bool:
        if self.symbol_type == SymbolType.ANONYMOUS:
            return True
        ns = self.containing_namespace
        if ns is not None and ns.is_anonymous:
            return True
        return False

    @property
    def is_implicit(self) -> bool:
        return self._implicit

    def shallow_clone(
        self, new_obj: Any, new_containing_scope: "Scope", symbol_type: SymbolType
    ) -> "Symbol":
        return self.__class__(self.name, symbol_type, new_obj, new_containing_scope)

    @property
    def obj_id(self) -> int:
        return id(self.obj)

    @property
    def obj_len(self) -> Optional[int]:
        try:
            if not self.is_obj_lazy_module and hasattr(self.obj, "__len__"):
                return len(self.obj)
        except Exception:
            pass
        return None

    @property
    def obj_type(self) -> Type[Any]:
        return type(self.obj)

    @property
    def is_immutable(self) -> bool:
        return self.obj_type in self.IMMUTABLE_TYPES

    @property
    def is_mutation_virtual_symbol(self) -> bool:
        return self.name == self.IPYFLOW_MUTATION_VIRTUAL_SYMBOL_NAME

    @property
    def is_implicit_virtual(self) -> bool:
        return self.name in (
            self.IPYFLOW_MUTATION_VIRTUAL_SYMBOL_NAME,
            self.IPYFLOW_ITER_VIRTUAL_SYMBOL_NAME,
        )

    @property
    def is_underscore(self) -> bool:
        return self.name == "_" and self.containing_scope.is_global

    @property
    def is_obj_lazy_module(self) -> bool:
        return self.obj_type is _LazyModule

    def get_type_annotation(self):
        return get_type_annotation(self.obj)

    def get_type_annotation_string(self) -> str:
        return make_annotation_string(self.get_type_annotation())

    @property
    def namespace(self) -> Optional["Namespace"]:
        return flow().namespaces.get(self.obj_id)

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
            return containing_ns.is_garbage
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
            containing_ns._subscript_symbol_by_name.pop(self.name, None)
        elif not self.is_subscript:
            self.containing_scope._symbol_by_name.pop(self.name, None)
        else:
            logger.warning(
                "could not find symbol %s in its scope %s", self, self.containing_scope
            )
        # TODO: remove from static / dynamic parent / children edges
        # need to keep this around for readable_name to work
        # self.containing_scope = None

    # def update_type(self, new_type):
    #     self.symbol_type = new_type
    #     if self.is_function:
    #         self.call_scope = self.containing_scope.make_child_scope(self.name)
    #     else:
    #         self.call_scope = None

    def update_obj_ref(self, obj: Any, refresh_cached: bool = True) -> None:
        if self._num_ipywidget_observers > 0:
            try:
                self.obj.unobserve_all()
            except Exception:
                pass
            self._num_ipywidget_observers = 0
        if self._num_mercury_widget_observers > 0:
            try:
                self._mercury_widgets_manager.get_widget(
                    self.obj.code_uid
                ).unobserve_all()
            except Exception:
                pass
            self._num_mercury_widget_observers = 0
        self._tombstone = False
        self._cached_out_of_sync = True
        if (
            flow().settings.mark_typecheck_failures_unsafe
            and self.cached_obj_type is not type(obj)
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
        if self.obj is None or self.obj is Symbol.NULL:
            return -1
        total = sys.getrefcount(self.obj) - 1
        total -= len(flow().aliases.get(self.obj_id, []))
        ns = self.namespace
        if ns is not None and ns.obj is self.obj:
            total -= 1
        ns = self.containing_namespace
        if ns is not None and not ns.is_garbage:
            total += 1
        return total

    def _should_cancel_propagation(self, prev_obj: Optional[Any]) -> bool:
        if prev_obj is None:
            return False
        if (
            flow().blocked_reactive_timestamps_by_symbol.get(self, -1)
            == self.timestamp.cell_num
        ):
            return False
        if not self._cached_out_of_sync or self.obj_id == self.cached_obj_id:
            return True
        if self.obj is None or prev_obj is Symbol.NULL:
            return self.obj is None and prev_obj is Symbol.NULL
        return False

    def _handle_aliases(self):
        cleanup_discard(flow().aliases, self.cached_obj_id, self)
        flow().aliases.setdefault(self.obj_id, set()).add(self)

    def update_stmt_node(
        self, stmt_node: Optional[Union[ast.stmt, ast.Lambda]]
    ) -> Optional[Union[ast.stmt, ast.Lambda]]:
        self.stmt_node = stmt_node
        self._funcall_live_symbols = None
        if self.is_function or (
            stmt_node is not None and isinstance(stmt_node, ast.Lambda)
        ):
            # TODO: in the case of lambdas, there will not necessarily be one
            #  symbol for a given statement. We need a more precise way to determine
            #  the symbol being called than by looking at the stmt in question.
            flow().statement_to_func_sym[id(stmt_node)] = self
            self.call_scope = self.containing_scope.make_child_scope(self.name)
            self.func_def_stmt = stmt_node
        return stmt_node

    def _refresh_cached_obj(self) -> None:
        self._cached_out_of_sync = False
        # don't keep an actual ref to avoid bumping refcount
        self.cached_obj_id = self.obj_id
        self.cached_obj_type = self.obj_type
        self.cached_obj_len = self.obj_len

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
    ) -> Generator[Tuple[ast.arg, List["Symbol"]], None, None]:
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
            if keyword_key is None or keyword_value is None:
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
            self.call_scope.upsert_symbol_for_name(  # type: ignore[union-attr]
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
            self.call_scope.upsert_symbol_for_name(  # type: ignore[union-attr]
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
                dep_introduced_pos = cells().at_timestamp(ts).position
                if dep_introduced_pos > pos:
                    continue
                for updated_ts in par.updated_timestamps:
                    if cells().at_timestamp(updated_ts).position > dep_introduced_pos:
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

    def _is_underscore_or_simple_assign(self, new_deps: Set["Symbol"]) -> bool:
        if self.is_underscore:
            # FIXME: distinguish between explicit assignment to _ from user and implicit assignment from kernel
            return True
        if not isinstance(self.stmt_node, (ast.Assign, ast.AnnAssign)):
            return False
        if len(new_deps) != 1:
            return False
        only_dep: Symbol = next(iter(new_deps))
        # obj ids can get reused for anon symbols like literals
        return not only_dep.is_anonymous and self.obj_id == only_dep.obj_id

    def update_deps(
        self,
        new_deps: Set["Symbol"],
        prev_obj: Any = None,
        overwrite: bool = True,
        mutated: bool = False,
        deleted: bool = False,
        propagate_to_namespace_descendents: bool = False,
        propagate: bool = True,
        refresh: bool = True,
        is_cascading_reactive: Optional[bool] = None,
    ) -> None:
        flow_ = flow()
        if self.is_import and self.obj_id == self.cached_obj_id:
            # skip updates for imported symbols; just bump the version
            self.refresh()
            return
        if overwrite and not self.is_globally_accessible:
            self.watchpoints.clear()
        if mutated and self.is_immutable:
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
        if (
            (mutated or overwrite)
            and Timestamp.current().is_initialized
            and not self.is_immutable
            and not self.is_mutation_virtual_symbol
            and self.is_globally_accessible
            and not self.is_underscore
            and not self.is_implicit
            and self.obj_type is not type
            and not self.is_class
            and self.namespace is not None
        ):
            self.namespace.upsert_symbol_for_name(
                self.IPYFLOW_MUTATION_VIRTUAL_SYMBOL_NAME, object(), propagate=False
            )
        propagate = propagate and (
            mutated or deleted or not self._should_cancel_propagation(prev_obj)
        )
        try:
            prev_cell = cells().current_cell().prev_cell
        except KeyError:
            prev_cell = None
        prev_cell_ctr = -1 if prev_cell is None else prev_cell.cell_ctr
        if overwrite:
            self._cascading_reactive_cell_num = -1
            flow_.updated_reactive_symbols.discard(self)
            flow_.updated_deep_reactive_symbols.discard(self)
        if is_cascading_reactive is not None:
            is_cascading_reactive = is_cascading_reactive or any(
                sym.is_cascading_reactive_at_counter(prev_cell_ctr) for sym in new_deps
            )
        if is_cascading_reactive:
            self.bump_cascading_reactive_cell_num()
        if refresh:
            self.refresh(
                # rationale: if this is a mutation for which we have more precise information,
                # then we don't need to update the ns descendents as this will already have happened.
                # also don't update ns descendents for things like `a = b`
                refresh_descendent_namespaces=propagate
                and not (mutated and not propagate_to_namespace_descendents)
                and not self._is_underscore_or_simple_assign(new_deps),
            )
        if propagate:
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
            self._handle_possible_mercury_widget_creation()

    def mutate(
        self, deps: Optional[Set["Symbol"]] = None, propagate: bool = True
    ) -> None:
        self.update_deps(
            deps or set(),
            overwrite=False,
            mutated=True,
            propagate_to_namespace_descendents=propagate,
            refresh=propagate,
        )

    @property
    def _mercury_widgets_manager(self):
        if self.obj is None:
            return None
        if self.is_obj_lazy_module or not hasattr(self.obj, "code_uid"):
            return None
        try:
            return sys.modules.get(self.obj.__class__.__module__).WidgetsManager
        except Exception:
            return None

    def _handle_possible_widget_creation(self) -> None:
        if self.obj is None:
            return
        Widget = getattr(sys.modules.get("ipywidgets"), "Widget", None)
        if (
            Widget is None
            or self.is_obj_lazy_module
            or not isinstance(self.obj, Widget)
            or not hasattr(self.obj, "observe")
            or not hasattr(self.obj, "value")
        ):
            return
        self.namespaced().upsert_symbol_for_name(
            "value", getattr(self.obj, "value", None), set(), self.stmt_node
        )
        self.obj.observe(self._observe_widget)
        self._num_ipywidget_observers += 1

    def _handle_possible_mercury_widget_creation(self) -> None:
        WidgetsManager = self._mercury_widgets_manager
        if WidgetsManager is None:
            return
        widget = WidgetsManager.get_widget(self.obj.code_uid)
        self.namespaced().upsert_symbol_for_name(
            "value", getattr(widget, "value", None), set(), self.stmt_node
        )
        widget.observe(self._observe_widget)
        self._num_mercury_widget_observers += 1

    def _observe_widget(self, msg: Dict[str, Any]) -> None:
        if msg.get("name") != "value" or "new" not in msg:
            return
        ns = self.namespace
        if ns is None:
            return
        sym = ns.lookup_symbol_by_name_this_indentation("value")
        if sym is None:
            return
        newval = msg["new"]
        current_ts_cell = cells().at_timestamp(self._timestamp)
        current_ts_cell._extra_stmt = ast.parse(f"{sym.readable_name} = {newval}").body[
            0
        ]
        sym._override_ready_liveness_cell_num = flow().cell_counter() + 1
        sym._override_timestamp = Timestamp(
            self._timestamp.cell_num, current_ts_cell.num_original_stmts
        )
        sym.update_obj_ref(newval)
        statements().create_and_track(
            current_ts_cell._extra_stmt,
            timestamp=sym._override_timestamp,
            override=True,
        )
        with dynamic_slicing_context():
            flow().add_data_dep(
                sym._timestamp,
                sym._override_timestamp,
                sym,
            )
            flow().add_data_dep(
                sym._override_timestamp,
                sym._timestamp,
                sym,
            )
        self.debounced_exec_schedule(reactive=True)

    def debounced_exec_schedule(self, reactive: bool) -> None:
        if _debounced_exec_schedule(
            cells().at_timestamp(self.timestamp).cell_id, reactive=reactive
        ):
            flow().comm_manager.debounced_exec_schedule_pending = True

    def namespaced(self) -> "Namespace":
        ns = self.namespace
        if ns is not None:
            return ns
        return namespaces()(self.obj, self.name, parent_scope=self.containing_scope)

    def update_usage_info_one_timestamp(
        self,
        used_time: Timestamp,
        updated_time: Timestamp,
        is_static: bool,
    ) -> bool:
        flow_ = flow()
        is_usage = is_static or updated_time < used_time
        if is_usage:
            with slicing_context(is_static=is_static):
                flow_.add_data_dep(
                    used_time,
                    updated_time,
                    self,
                )
        if is_static:
            is_usage = cells().at_timestamp(updated_time).is_visible
        return is_usage

    def update_usage_info(
        self,
        used_time: Optional[Timestamp] = None,
        used_node: Optional[ast.AST] = None,
        exclude_ns: bool = False,
        is_static: bool = False,
        is_blocking: bool = False,
    ) -> "Symbol":
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
        timestamp_by_used_time = (
            self.timestamp_by_liveness_time
            if is_static
            else self.timestamp_by_used_time
        )
        if not is_blocking:
            is_usage = False
            ts_to_use = self._initialized_timestamp
            for updated_ts in sorted(self.updated_timestamps, reverse=True):
                if not updated_ts.is_initialized:
                    continue
                is_usage = self.update_usage_info_one_timestamp(
                    used_time,
                    updated_ts,
                    is_static=is_static,
                )
                if is_usage or not is_static:
                    break
            if is_usage and used_time.is_initialized:
                timestamp_by_used_time[used_time] = ts_to_use
                if used_node is not None:
                    self.used_node_by_used_time[used_time] = used_node
        if exclude_ns:
            return self
        for sym in self.get_namespace_symbols(recurse=True):
            sym.update_usage_info(
                used_time=used_time,
                used_node=None,
                exclude_ns=True,
                is_static=is_static,
                is_blocking=is_blocking,
            )
        return self

    def get_namespace_symbols(
        self, recurse: bool = False, seen: Optional[Set["Symbol"]] = None
    ) -> Generator["Symbol", None, None]:
        ns = self.namespace
        if ns is None:
            return
        if seen is None:
            seen = set()
        if self in seen:
            return
        seen.add(self)
        for sym in ns.all_symbols_this_indentation():
            yield sym
            if recurse:
                yield from sym.get_namespace_symbols(recurse=recurse, seen=seen)

    def _take_timestamp_snapshots(
        self, ts_ubound: Timestamp, seen: Optional[Set["Symbol"]] = None
    ) -> None:
        if seen is None:
            seen = set()
        if self in seen:
            return
        seen.add(self)
        self._snapshot_timestamps.append(self._timestamp)
        self._snapshot_timestamp_ubounds.append(ts_ubound)
        containing_ns = self.containing_namespace
        if containing_ns is None:
            return
        for alias in flow().aliases.get(containing_ns.obj_id, []):
            alias._take_timestamp_snapshots(ts_ubound, seen=seen)

    def refresh(
        self,
        take_timestamp_snapshots: bool = True,
        refresh_descendent_namespaces: bool = False,
        timestamp: Optional[Timestamp] = None,
        seen: Optional[Set["Symbol"]] = None,
    ) -> None:
        if seen is not None and self in seen:
            return
        orig_timestamp = self._timestamp
        self._updated_timestamps.add(orig_timestamp)
        self._timestamp = Timestamp.current() if timestamp is None else timestamp
        self._override_timestamp = None
        if take_timestamp_snapshots and (
            orig_timestamp < self._timestamp or len(self._snapshot_timestamps) == 0
        ):
            self._take_timestamp_snapshots(self._timestamp)
        self._temp_disable_warnings = False
        for cell in self.cells_where_live:
            cell.add_used_cell_counter(self, self._timestamp.cell_num)
        ns = self.containing_namespace
        if ns is not None:
            # logger.error("bump version of %s due to %s (value %s)", ns.full_path, self.full_path, self.obj)
            ns.max_descendent_timestamp = max(
                ns.max_descendent_timestamp, self._timestamp
            )
            flow_ = flow()
            for alias in flow_.aliases.get(ns.obj_id, []):
                for cell in alias.cells_where_deep_live:
                    cell.add_used_cell_counter(alias, self._timestamp.cell_num)
        self.namespace_waiting_symbols.clear()
        if not refresh_descendent_namespaces:
            return
        if seen is None:
            seen = set()
        seen.add(self)
        ns = self.namespace
        if ns is None:
            return
        for sym in ns.all_symbols_this_indentation(exclude_class=True):
            # this is to handle cases like `x = x.mutate(42)`, where
            # we could have changed some member of x but returned the
            # original object -- in this case, just assume that all
            # the stale namespace descendents are no longer stale, as
            # this is likely the user intention. For an example, see
            # `test_external_object_update_propagates_to_stale_namespace_symbols()`
            # in `test_frontend_checker.py`
            # logger.error(
            #     "refresh %s due to %s (value %s) via namespace %s",
            #     sym.full_path,
            #     self.full_path,
            #     self.obj,
            #     ns.full_path,
            # )
            sym.refresh(
                refresh_descendent_namespaces=True,
                timestamp=self._timestamp,
                take_timestamp_snapshots=False,
                seen=seen,
            )

    def resync_if_necessary(self, refresh: bool) -> None:
        if not self.containing_scope.is_global:
            return
        try:
            obj = shell().user_ns[self.name]
        except Exception:
            # cinder runtime can throw an exception here due to lazy imports that fail
            return
        if self.obj is not obj:
            flow_ = flow()
            if len(flow_.aliases.get(id(obj), [])) == 0:
                aliases_to_check = flow_.aliases.get(
                    self.cached_obj_id or -1, set()
                ) | flow_.aliases.get(self.obj_id, set())
            else:
                aliases_to_check = set()
            for alias in aliases_to_check:
                if alias.is_implicit_virtual:
                    continue
                containing_namespace = alias.containing_namespace
                if containing_namespace is None:
                    continue
                containing_obj = containing_namespace.obj
                if containing_obj is None:
                    continue
                # TODO: handle dict case too
                if isinstance(containing_obj, list) and containing_obj[-1] is obj:
                    containing_namespace._subscript_symbol_by_name.pop(alias.name, None)
                    alias.name = len(containing_obj) - 1
                    alias.update_obj_ref(obj)
                    containing_namespace._subscript_symbol_by_name[alias.name] = alias
            cleanup_discard(flow_.aliases, self.cached_obj_id, self)
            cleanup_discard(flow_.aliases, self.obj_id, self)
            flow_.aliases.setdefault(id(obj), set()).add(self)
            self.update_obj_ref(obj)
        elif self.obj_len != self.cached_obj_len:
            self._refresh_cached_obj()
        else:
            return
        if refresh:
            self.refresh()

    _MAX_MEMOIZE_COMPARABLE_SIZE = 10**6

    @staticmethod
    def _equal(obj1: Any, obj2: Any) -> bool:
        return obj1 == obj2

    @staticmethod
    def _array_equal(obj1: Any, obj2: Any) -> bool:
        import numpy as np

        try:
            return np.alltrue(obj1 == obj2)  # type: ignore
        except Exception:
            return False

    @staticmethod
    def _dataframe_equal(obj1: Any, obj2: Any) -> bool:
        try:
            return obj1.equals(obj2)  # type: ignore
        except Exception:
            return False

    @staticmethod
    def _make_list_eq(
        eqs: List[Callable[[Any, Any], bool]],
    ) -> Callable[[List[Any], List[Any]], bool]:
        def list_eq(lst1: List[Any], lst2: List[Any]) -> bool:
            for eq, obj1, obj2 in zip(eqs, lst1, lst2):
                if not eq(obj1, obj2):
                    return False
            return True

        return list_eq

    @classmethod
    def make_memoize_comparable_for_obj(
        cls, obj: Any, seen_ids: Set[int]
    ) -> Tuple[Any, Optional[Callable[[Any, Any], bool]], int]:
        if isinstance(obj, (bool, bytes, bytearray, int, float, str)):
            return obj, cls._equal, 1
        if not isinstance(obj, tuple):
            if id(obj) in seen_ids:
                return cls.NULL, None, -1
            seen_ids.add(id(obj))
        if isinstance(obj, (dict, frozenset, list, set, tuple)):
            size = 0
            comp = []
            eqs: List[Callable[[Any, Any], bool]] = []
            if isinstance(obj, dict):
                iterable: "Iterable[Any]" = sorted(obj.items())
            else:
                iterable = obj
            for inner in iterable:
                inner_comp, inner_eq, inner_size = cls.make_memoize_comparable_for_obj(
                    inner, seen_ids
                )
                if inner_comp is cls.NULL or inner_eq is None:
                    return cls.NULL, None, -1
                size += inner_size + 1
                if size > cls._MAX_MEMOIZE_COMPARABLE_SIZE:
                    return cls.NULL, None, -1
                comp.append(inner_comp)
                eqs.append(inner_eq)
            if all(eq is cls._equal for eq in eqs):
                iter_eq: Callable[[Any, Any], bool] = cls._equal
            elif isinstance(obj, (frozenset, set)):
                return cls.NULL, None, -1
            else:
                iter_eq = cls._make_list_eq(eqs)
            ret = frozenset(comp) if isinstance(obj, (frozenset, set)) else comp
            return ret, iter_eq, size
        elif type(obj) in (type, FunctionType):
            # try to determine it based on the symbol
            for sym in flow().aliases.get(id(obj), []):
                comp, eq = sym.make_memoize_comparable(seen_ids=seen_ids)
                if comp is not cls.NULL and eq is not None:
                    return comp, eq, 1
            return cls.NULL, None, -1
        else:
            # hacks to check if they are arrays, dataframes, etc without explicitly importing these
            module = getattr(type(obj), "__module__", "")
            if module.startswith("numpy"):
                name = getattr(type(obj), "__name__", "")
                if name.endswith("ndarray"):
                    return obj, cls._array_equal, obj.size
                else:
                    numpy = sys.modules.get("numpy")
                    if numpy is not None and isinstance(obj, numpy.number):
                        return obj, cls._equal, 1
            elif module.startswith(("modin", "pandas")):
                name = getattr(type(obj), "__name__", "")
                if name.endswith(("DataFrame", "Series")):
                    return obj, cls._dataframe_equal, obj.size
            elif module.startswith("ipywidgets"):
                ipywidgets = sys.modules.get("ipywidgets")
                if (
                    ipywidgets is not None
                    and isinstance(obj, ipywidgets.Widget)
                    and hasattr(obj, "value")
                ):
                    return obj.value, cls._equal, 1
            return cls.NULL, None, -1

    def make_memoize_comparable(
        self, seen_ids: Optional[Set[int]] = None
    ) -> Tuple[Any, Optional[Callable[[Any, Any], bool]]]:
        if seen_ids is None:
            seen_ids = set()
        if isinstance(
            self.stmt_node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            comps = [astunparse.unparse(self.stmt_node)]
            for sym in sorted(self.parents.keys()):
                par_comp, eq = sym.make_memoize_comparable(seen_ids=seen_ids)
                if par_comp is self.NULL or eq is not self._equal:
                    return self.NULL, None
                comps.append(par_comp)
            return comps, self._equal
        obj, eq, size = self.make_memoize_comparable_for_obj(self.obj, seen_ids)
        if size > self._MAX_MEMOIZE_COMPARABLE_SIZE:
            return self.NULL, None
        else:
            return obj, eq


if len(_SymbolContainer) == 0:
    _SymbolContainer.append(Symbol)
else:
    _SymbolContainer[0] = Symbol
