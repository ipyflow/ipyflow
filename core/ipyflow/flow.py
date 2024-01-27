# -*- coding: utf-8 -*-
import ast
import logging
import os
import sys
import textwrap
from collections import defaultdict
from contextlib import contextmanager
from types import FrameType
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    Iterable,
    List,
    Optional,
    Set,
    Tuple,
    Union,
    cast,
)

import pyccolo as pyc
from ipykernel.comm import Comm
from ipykernel.ipkernel import IPythonKernel
from pyccolo.tracer import PYCCOLO_DEV_MODE_ENV_VAR

from ipyflow import singletons
from ipyflow.analysis.resolved_symbols import ResolvedSymbol
from ipyflow.analysis.symbol_ref import SymbolRef
from ipyflow.config import (
    ColorScheme,
    DataflowSettings,
    ExecutionMode,
    ExecutionSchedule,
    FlowDirection,
    Highlights,
    Interface,
    MutableDataflowSettings,
    ReactivityMode,
)
from ipyflow.data_model.cell import Cell, cells
from ipyflow.data_model.namespace import Namespace
from ipyflow.data_model.scope import Scope
from ipyflow.data_model.statement import statements
from ipyflow.data_model.symbol import Symbol
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.frontend import FrontendCheckerResult
from ipyflow.line_magics import make_line_magic
from ipyflow.singletons import shell
from ipyflow.slicing.context import (
    SlicingContext,
    slicing_ctx_var,
    static_slicing_context,
)
from ipyflow.tracing.ipyflow_tracer import DataflowTracer
from ipyflow.tracing.watchpoint import Watchpoint
from ipyflow.types import IdType, SupportedIndexType

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class NotebookFlow(singletons.NotebookFlow):
    """Holds all the state necessary to capture dataflow in Jupyter notebooks."""

    def __init__(self, **kwargs) -> None:
        super().__init__()
        cells().clear()
        statements().clear()
        config = shell().config.ipyflow
        self._line_magic = make_line_magic(self)
        self.settings: DataflowSettings = DataflowSettings(
            test_context=kwargs.pop("test_context", False),
            mark_waiting_symbol_usages_unsafe=kwargs.pop(
                "mark_waiting_symbol_usages_unsafe",
                getattr(config, "mark_waiting_symbol_usages_unsafe", True),
            ),
            mark_typecheck_failures_unsafe=kwargs.pop(
                "mark_typecheck_failures_unsafe",
                getattr(config, "mark_typecheck_failures_unsafe", False),
            ),
            mark_phantom_cell_usages_unsafe=kwargs.pop(
                "mark_phantom_cell_usages_unsafe",
                getattr(config, "mark_phantom_cell_usages_unsafe", False),
            ),
        )
        interface = kwargs.pop("interface", Interface.UNKNOWN)
        self.mut_settings: MutableDataflowSettings = MutableDataflowSettings(
            dataflow_enabled=kwargs.pop("dataflow_enabled", True),
            trace_messages_enabled=kwargs.pop("trace_messages_enabled", False),
            highlights=kwargs.pop("highlights", Highlights.EXECUTED),
            interface=kwargs.pop("interface", Interface.UNKNOWN),
            static_slicing_enabled=kwargs.pop(
                "static_slicing_enabled",
                getattr(config, "static_slicing_enabled", True),
            ),
            dynamic_slicing_enabled=kwargs.pop(
                "dynamic_slicing_enabled",
                getattr(config, "dynamic_slicing_enabled", True),
            ),
            exec_mode=ExecutionMode(
                kwargs.pop(
                    "exec_mode",
                    getattr(config, "exec_mode", ExecutionMode.NORMAL),
                )
            ),
            exec_schedule=ExecutionSchedule(
                kwargs.pop(
                    "exec_schedule",
                    getattr(config, "exec_schedule", ExecutionSchedule.LIVENESS_BASED),
                )
            ),
            flow_order=FlowDirection(
                kwargs.pop(
                    "flow_direction",
                    getattr(config, "flow_direction", FlowDirection.IN_ORDER),
                )
            ),
            reactivity_mode=ReactivityMode(
                kwargs.pop(
                    "reactivity_mode",
                    getattr(config, "reactivity_mode", ReactivityMode.BATCH),
                )
            ),
            push_reactive_updates=kwargs.pop(
                "push_reactive_updates",
                getattr(config, "push_reactive_updates", True),
            ),
            push_reactive_updates_to_cousins=kwargs.pop(
                "push_reactive_updates_to_cousins",
                getattr(config, "push_reactive_updates_to_cousins", False),
            ),
            pull_reactive_updates=kwargs.pop(
                "pull_reactive_updates",
                getattr(
                    config, "pull_reactive_updates", interface == Interface.JUPYTERLAB
                ),
            ),
            color_scheme=ColorScheme(
                kwargs.pop(
                    "color_scheme",
                    getattr(config, "color_scheme", ColorScheme.NORMAL),
                )
            ),
            warn_out_of_order_usages=kwargs.pop(
                "warn_out_of_order_usages",
                getattr(config, "warn_out_of_order_usages", False),
            ),
            lint_out_of_order_usages=kwargs.pop(
                "lint_out_of_order_usages",
                getattr(config, "lint_out_of_order_usages", False),
            ),
            syntax_transforms_enabled=kwargs.pop(
                "syntax_transforms_enabled",
                getattr(
                    config, "syntax_transforms_enabled", sys.version_info >= (3, 8)
                ),
            ),
            syntax_transforms_only=kwargs.pop(
                "syntax_transforms_only",
                getattr(config, "syntax_transforms_only", False),
            ),
            max_external_call_depth_for_tracing=kwargs.pop(
                "max_external_call_depth_for_tracing",
                getattr(config, "max_external_call_depth_for_tracing", 3),
            ),
            is_dev_mode=kwargs.pop(
                "is_dev_mode",
                getattr(
                    config,
                    "is_dev_mode",
                    os.getenv(PYCCOLO_DEV_MODE_ENV_VAR) == "1",
                ),
            ),
        )
        if self.is_dev_mode:
            os.environ[PYCCOLO_DEV_MODE_ENV_VAR] = "1"
        else:
            os.environ.pop(PYCCOLO_DEV_MODE_ENV_VAR, None)
        # Note: explicitly adding the types helps PyCharm intellisense
        self.namespaces: Dict[int, Namespace] = {}
        self.aliases: Dict[int, Set[Symbol]] = {}
        self.starred_import_modules: Set[str] = set()
        self.stmt_deferred_static_parents: Dict[
            Timestamp, Dict[Timestamp, Set[Symbol]]
        ] = {}
        self.global_scope: Scope = Scope()
        self.virtual_symbols: Scope = Scope()
        self._virtual_symbols_inited: bool = False
        self.updated_symbols: Set[Symbol] = set()
        self.updated_reactive_symbols: Set[Symbol] = set()
        self.updated_deep_reactive_symbols: Set[Symbol] = set()
        self.updated_reactive_symbols_last_cell: Set[Symbol] = set()
        self.updated_deep_reactive_symbols_last_cell: Set[Symbol] = set()
        self.active_watchpoints: List[Tuple[Tuple[Watchpoint, ...], Symbol]] = []
        self.blocked_reactive_timestamps_by_symbol: Dict[Symbol, int] = {}
        self.statement_to_func_sym: Dict[int, Symbol] = {}
        self.active_cell_id: Optional[IdType] = None
        self.waiter_usage_detected = False
        self.out_of_order_usage_detected_counter: Optional[int] = None
        self._prev_cell_waiting_symbols: Set[Symbol] = set()
        self._cell_name_to_cell_num_mapping: Dict[str, int] = {}
        self._exception_raised_during_execution: Union[None, Exception, str] = None
        self._last_exception_raised: Union[None, str, Exception] = None
        self.exception_counter: int = 0
        self._saved_debug_message: Optional[str] = None
        self.min_timestamp = -1
        self.min_cascading_reactive_cell_num = -1
        self._tags: Tuple[str, ...] = ()
        self.last_executed_content: Optional[str] = None
        self.last_executed_cell_id: Optional[IdType] = None
        self.tracked_timestamps: Dict[str, Timestamp] = {}
        self._comm_handlers: Dict[
            str, Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]
        ] = {}
        self.register_comm_handler("change_active_cell", self.handle_change_active_cell)
        self.register_comm_handler(
            "compute_exec_schedule", self.handle_compute_exec_schedule
        )
        self.register_comm_handler(
            "notify_content_changed", self.handle_notify_content_changed
        )
        self.register_comm_handler("reactivity_cleanup", self.handle_reactivity_cleanup)
        self.register_comm_handler("refresh_symbols", self.handle_refresh_symbols)
        self.register_comm_handler("upsert_symbol", self.handle_upsert_symbol)
        self.register_comm_handler("get_code", self.handle_get_code)
        self.register_comm_handler(
            "get_last_updated_cell_id", self.handle_get_last_updated_cell_id
        )
        self.register_comm_handler("bump_timestamp", self.handle_bump_timestamp)
        self.register_comm_handler(
            "register_dynamic_comm_handler", self.handle_register_dynamic_comm_handler
        )
        self.debounced_exec_schedule_pending = False
        self.fs: Namespace = None
        self.display_sym: Symbol = None
        self.fake_edge_sym: Symbol = None
        self._override_child_cell: Optional[Cell] = None
        self._comm: Optional[Comm] = None
        self._prev_cell_metadata_by_id: Optional[Dict[IdType, Dict[str, Any]]] = None
        self._prev_order_idx_by_id: Optional[Dict[IdType, int]] = None
        self._min_new_ready_cell_counter = -1
        self._min_forced_reactive_cell_counter = -1

    def register_comm_target(self, kernel: IPythonKernel) -> None:
        kernel.comm_manager.register_target(__package__, self._comm_target)

    def init_virtual_symbols(self) -> None:
        if self._virtual_symbols_inited:
            return
        self.fs = Namespace(Namespace.FILE_SYSTEM, "fs")
        self.display_sym = self.virtual_symbols.upsert_symbol_for_name(
            "display", Symbol.DISPLAY, propagate=False, implicit=True
        )
        self.fake_edge_sym = self.virtual_symbols.upsert_symbol_for_name(
            "fake_edge_sym", object(), propagate=False, implicit=True
        )
        self._virtual_symbols_inited = True

    def _initialize_cell_parents(
        self, cell_parents: Optional[Dict[IdType, List[IdType]]]
    ) -> None:
        if cell_parents is None:
            return
        with static_slicing_context():
            for child, parents in cell_parents.items():
                try:
                    child_cell = cells().from_id(child)
                except KeyError:
                    continue
                for parent in parents:
                    try:
                        parent_cell = cells().from_id(parent)
                    except KeyError:
                        continue
                    child_cell.add_parent_edge(parent_cell, self.fake_edge_sym)

    def initialize(
        self,
        *,
        interface: Optional[str] = None,
        cell_metadata_by_id: Optional[Dict[str, Any]] = None,
        cell_parents: Optional[Dict[IdType, List[IdType]]] = None,
        **kwargs,
    ) -> None:
        config = shell().config.ipyflow
        iface = Interface(interface)
        if self.mut_settings.interface == iface:
            return
        self.mut_settings.interface = iface
        self.mut_settings.dataflow_enabled = getattr(
            config, "dataflow_enabled", kwargs.get("dataflow_enabled", True)
        )
        self.mut_settings.syntax_transforms_enabled = getattr(
            config,
            "syntax_transforms_enabled",
            kwargs.get("syntax_transforms_enabled", sys.version_info >= (3, 8)),
        )
        self.mut_settings.syntax_transforms_only = getattr(
            config,
            "syntax_transforms_only",
            kwargs.get("syntax_transforms_only", False),
        )
        self.mut_settings.exec_mode = ExecutionMode(
            getattr(
                config,
                "exec_mode",
                kwargs.get("exec_mode"),
            )
        )
        self.mut_settings.exec_schedule = ExecutionSchedule(
            getattr(
                config,
                "exec_schedule",
                kwargs.get("exec_schedule"),
            )
        )
        self.mut_settings.flow_order = FlowDirection(
            getattr(
                config,
                "flow_direction",
                kwargs.get("flow_direction"),
            )
        )
        self.mut_settings.highlights = Highlights(
            getattr(config, "highlights", kwargs.get("highlights"))
        )
        self.mut_settings.reactivity_mode = ReactivityMode(
            getattr(
                config,
                "reactivity_mode",
                kwargs.get("reactivity_mode"),
            )
        )
        push_reactive_updates = getattr(
            config,
            "push_reactive_updates",
            kwargs.get("push_reactive_updates"),
        )
        push_reactive_updates_to_cousins = getattr(
            config,
            "push_reactive_updates_to_cousins",
            kwargs.get("push_reactive_updates_to_cousins"),
        )
        pull_reactive_updates = getattr(
            config,
            "pull_reactive_updates",
            kwargs.get("pull_reactive_updates", iface == Interface.JUPYTERLAB),
        )
        if push_reactive_updates is not None:
            self.mut_settings.push_reactive_updates = push_reactive_updates
        if push_reactive_updates_to_cousins is not None:
            self.mut_settings.push_reactive_updates = push_reactive_updates_to_cousins
        if pull_reactive_updates is not None:
            self.mut_settings.pull_reactive_updates = pull_reactive_updates
        self.mut_settings.color_scheme = ColorScheme(
            getattr(
                config,
                "color_scheme",
                kwargs.get("color_scheme"),
            )
        )
        self.mut_settings.max_external_call_depth_for_tracing = getattr(
            config,
            "max_external_call_depth_for_tracing",
            kwargs.get(
                "max_external_call_depth_for_tracing",
                self.mut_settings.max_external_call_depth_for_tracing,
            ),
        )
        self.mut_settings.is_dev_mode = getattr(
            config,
            "is_dev_mode",
            kwargs.get("is_dev_mode", self.mut_settings.is_dev_mode),
        )
        if self.is_dev_mode:
            os.environ[PYCCOLO_DEV_MODE_ENV_VAR] = "1"
        else:
            os.environ.pop(PYCCOLO_DEV_MODE_ENV_VAR, None)
        self.init_virtual_symbols()
        if cell_metadata_by_id is not None:
            self.handle_notify_content_changed(
                {"cell_metadata_by_id": cell_metadata_by_id},
                is_reactively_executing=True,
            )
        self._initialize_cell_parents(cell_parents)

    @property
    def is_dev_mode(self) -> bool:
        return self.mut_settings.is_dev_mode

    @property
    def is_test(self) -> bool:
        return self.settings.test_context

    @property
    def trace_messages_enabled(self) -> bool:
        return self.mut_settings.trace_messages_enabled

    @trace_messages_enabled.setter
    def trace_messages_enabled(self, new_val) -> None:
        self.mut_settings.trace_messages_enabled = new_val

    def get_first_full_symbol(self, obj_id: int) -> Optional[Symbol]:
        for alias in self.aliases.get(obj_id, []):
            if not alias.is_anonymous:
                return alias
        return None

    @staticmethod
    def cell_counter() -> int:
        return cells().exec_counter()

    def min_new_ready_cell_counter(self) -> int:
        return max(
            self._min_new_ready_cell_counter, self.cell_counter(), self.min_timestamp
        )

    def min_forced_reactive_cell_counter(self) -> int:
        return max(
            self._min_forced_reactive_cell_counter,
            self.min_timestamp,
        )

    def bump_min_forced_reactive_counter(self) -> None:
        self._min_forced_reactive_cell_counter = self.cell_counter()

    @contextmanager
    def override_child_cell(self, cell: Cell) -> Generator[None, None, None]:
        orig_override = self._override_child_cell
        try:
            self._override_child_cell = cell
            yield
        finally:
            self._override_child_cell = orig_override

    def add_data_dep(
        self,
        child: Timestamp,
        parent: Timestamp,
        sym: Symbol,
    ) -> None:
        assert parent.is_initialized
        child_cell = self._override_child_cell or cells().at_timestamp(child)
        child_cell.used_symbols.add(sym)
        parent_cell = cells().at_timestamp(parent)
        # if it has already run, don't add the edge
        if child_cell.is_current and parent_cell.is_current:
            child_cell.add_parent_edge(parent_cell, sym)
        if not child.is_initialized:
            return
        if slicing_ctx_var.get() == SlicingContext.DYNAMIC:
            statements().at_timestamp(child).add_parent_edge(
                statements().at_timestamp(parent), sym
            )
        else:
            self.stmt_deferred_static_parents.setdefault(child, {}).setdefault(
                parent, set()
            ).add(sym)

    def is_updated_reactive(self, sym: Symbol) -> bool:
        return (
            sym in self.updated_reactive_symbols
            or sym in self.updated_reactive_symbols_last_cell
        )

    def is_updated_deep_reactive(self, sym: Symbol) -> bool:
        return (
            sym in self.updated_deep_reactive_symbols
            or sym in self.updated_deep_reactive_symbols_last_cell
        )

    def reset_cell_counter(self):
        # only called in test context
        for sym in self.all_symbols():
            sym.updated_timestamps.clear()
            sym._timestamp = (
                sym._max_inner_timestamp
            ) = sym.required_timestamp = Timestamp.uninitialized()
            sym.timestamp_by_used_time.clear()
            sym.timestamp_by_liveness_time.clear()
        cells().clear()
        statements().clear()

    def get_and_set_exception_raised_during_execution(
        self, new_val: Union[None, str, Exception] = None
    ) -> Union[None, str, Exception]:
        ret = self._exception_raised_during_execution
        self._exception_raised_during_execution = new_val
        if new_val is not None:
            self._last_exception_raised = new_val
            self.exception_counter += 1
        return ret

    def reset_exception_counter(self) -> Tuple[int, Union[None, str, Exception]]:
        ret = self.exception_counter, self._last_exception_raised
        self.exception_counter = 0
        self._last_exception_raised = None
        return ret

    def get_position(self, frame: FrameType) -> Tuple[int, int]:
        try:
            cell_num = self._cell_name_to_cell_num_mapping.get(
                frame.f_code.co_filename, None
            )
            if cell_num is None:
                cell_num = self.cell_counter()
            return cell_num, frame.f_lineno
        except KeyError as e:
            logger.error(
                "key error while retrieving cell for %s", frame.f_code.co_filename
            )
            raise e

    def set_name_to_cell_num_mapping(self, frame: FrameType):
        self._cell_name_to_cell_num_mapping[
            frame.f_code.co_filename
        ] = cells().exec_counter()

    def is_cell_file(self, fname: str) -> bool:
        return fname in self._cell_name_to_cell_num_mapping

    def set_active_cell(self, cell_id: IdType) -> None:
        self.active_cell_id = cell_id

    def set_tags(self, tags: Tuple[str, ...]) -> None:
        self._tags = tags

    def _comm_target(self, comm: Comm, open_msg: Dict[str, Any]) -> None:
        @comm.on_msg
        def _responder(msg):
            request = msg["content"]["data"]
            if (
                request.get("type") == "compute_exec_schedule"
                and self.debounced_exec_schedule_pending
            ):
                return
            self.handle(request, comm=comm)

        self._comm = comm
        self.initialize(**open_msg.get("content", {}).get("data", {}))
        comm.send({"type": "establish", "success": True})

    @staticmethod
    def _create_untracked_cells_for_content(content_by_cell_id: Dict[IdType, str]):
        for cell_id, content in content_by_cell_id.items():
            cell = cells().from_id_nullable(cell_id)
            if cell is not None:
                continue
            cells().create_and_track(cell_id, content, (), bump_cell_counter=False)

    def _recompute_ast_for_cells(
        self, content_by_cell_id: Dict[IdType, str], force: bool = False
    ) -> bool:
        should_recompute_exec_schedule = force
        for cell_id, content in content_by_cell_id.items():
            if should_recompute_exec_schedule:
                break
            cell = cells().from_id_nullable(cell_id)
            if cell is None:
                continue
            is_same_content = cell.current_content == content
            is_same_counter = cell.cell_ctr == (cell.last_check_cell_ctr or 0)
            if not is_same_content or not is_same_counter:
                should_recompute_exec_schedule = True
        if not should_recompute_exec_schedule:
            return False
        should_recompute_exec_schedule = False
        for cell_id, content in content_by_cell_id.items():
            cell = cells().from_id_nullable(cell_id)
            if cell is None:
                continue
            prev_content = cell.current_content
            try:
                cell.current_content = content
                cell.to_ast()
                result = cell.check_and_resolve_symbols(
                    update_liveness_time_versions=True,
                )
                if cell.last_check_result is None:
                    prev_resolved_live_syms: Set[ResolvedSymbol] = set()
                else:
                    prev_resolved_live_syms = cell.last_check_result.live
                prev_live_syms = {resolved.sym for resolved in prev_resolved_live_syms}
                live_syms: Set[Symbol] = {resolved.sym for resolved in result.live}
                cell.static_removed_symbols |= prev_live_syms
                cell.static_removed_symbols -= live_syms
                cell.last_check_content = cell.current_content
                cell.last_check_result = result
                cell.last_check_cell_ctr = cell.cell_ctr
                should_recompute_exec_schedule = True
            except SyntaxError:
                cell.current_content = prev_content
        return should_recompute_exec_schedule

    def register_comm_handler(
        self,
        msg_type: str,
        handler: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
        overwrite: bool = False,
    ) -> None:
        if msg_type in self._comm_handlers and not overwrite:
            raise ValueError("handler already registered for msg type of %s" % msg_type)
        self._comm_handlers[msg_type] = handler

    def handle(self, request: Dict[str, Any], comm=None) -> None:
        request_type = request["type"]
        handler = self._comm_handlers.get(request_type)
        if handler is None:
            dbg_msg = "Unsupported request type for request %s" % request
            logger.error(dbg_msg)
            self._saved_debug_message = dbg_msg
            return
        try:
            response = handler(request)
        except Exception as e:
            response = {
                "success": False,
                "error": str(e),
            }
            if self.is_dev_mode:
                logger.exception("exception during comm handler execution")
        if comm is None:
            comm = self._comm
        if comm is None:
            return
        if response is None:
            response = {}
        response["type"] = response.get("type", request_type)
        response["success"] = response.get("success", True)
        try:
            comm.send(response)
        except TypeError as e:
            raise Exception(
                "unable to serialize response for request of type %s" % request_type
            ) from e

    def handle_change_active_cell(
        self, request: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        self.set_active_cell(request["active_cell_id"])
        return None

    def handle_notify_content_changed(
        self, request: Dict[str, Any], is_reactively_executing: bool = False
    ) -> Optional[Dict[str, Any]]:
        cell_metadata_by_id = request.get(
            "cell_metadata_by_id", self._prev_cell_metadata_by_id
        )
        if cell_metadata_by_id is None:
            # bail if we don't have this
            return {"success": False, "error": "null value for cell metadata"}
        is_cell_structure_change = self._prev_cell_metadata_by_id is None or len(
            self._prev_cell_metadata_by_id
        ) != len(cell_metadata_by_id)
        self._prev_cell_metadata_by_id = cell_metadata_by_id
        cell_metadata_by_id = {
            cell_id: metadata
            for cell_id, metadata in cell_metadata_by_id.items()
            if metadata["type"] == "code"
            or metadata.get("override_live_refs")
            or metadata.get("override_dead_refs")
        }
        order_index_by_id = {
            cell_id: metadata["index"]
            for cell_id, metadata in cell_metadata_by_id.items()
        }
        is_cell_structure_change = (
            is_cell_structure_change or self._prev_order_idx_by_id != order_index_by_id
        )
        prev_order_idx_by_id = self._prev_order_idx_by_id
        self._prev_order_idx_by_id = order_index_by_id
        content_by_cell_id = {
            cell_id: metadata["content"]
            for cell_id, metadata in cell_metadata_by_id.items()
        }
        override_live_refs_by_cell_id = {
            cell_id: metadata["override_live_refs"]
            for cell_id, metadata in cell_metadata_by_id.items()
            if metadata.get("override_live_refs")
        }
        override_dead_refs_by_cell_id = {
            cell_id: metadata["override_dead_refs"]
            for cell_id, metadata in cell_metadata_by_id.items()
            if metadata.get("override_dead_refs")
        }
        cells().set_cell_positions(order_index_by_id)
        cells().set_override_refs(
            override_live_refs_by_cell_id, override_dead_refs_by_cell_id
        )
        should_recompute_exec_schedule = (
            not is_reactively_executing
            and self._recompute_ast_for_cells(
                content_by_cell_id, force=order_index_by_id != prev_order_idx_by_id
            )
        ) or is_cell_structure_change
        placeholder_cells = cells().with_placeholder_ids()
        if len(placeholder_cells) > 0:
            for _, cell_id in sorted(
                (idx, cell_id) for cell_id, idx in order_index_by_id.items()
            ):
                if cells().has_id(cell_id):
                    continue
                content = content_by_cell_id[cell_id]
                for candidate in list(placeholder_cells):
                    if candidate.current_content == content:
                        candidate.update_id(cell_id)
                        placeholder_cells.remove(candidate)
                        break
        self._create_untracked_cells_for_content(content_by_cell_id)
        if should_recompute_exec_schedule:
            return self.handle_compute_exec_schedule(
                request, notify_content_changed=False, allow_new_ready=False
            )
        else:
            return None

    def _handle_compute_exec_schedule_impl(
        self,
        request: Dict[str, Any],
        notify_content_changed: bool = True,
        allow_new_ready: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if not self.mut_settings.dataflow_enabled:
            return {"success": False, "error": "dataflow not enabled"}
        is_reactively_executing = request.get("is_reactively_executing", False)
        if self.active_cell_id is None:
            self.set_active_cell(request.get("executed_cell_id"))
            if self.active_cell_id is not None:
                prev_cell = cells().current_cell()
                if prev_cell.is_placeholder_id:
                    prev_cell.update_id(self.active_cell_id)
        if notify_content_changed and request.get("notify_content_changed", True):
            self.handle_notify_content_changed(
                request, is_reactively_executing=is_reactively_executing
            )
        try:
            self._add_parents_for_override_live_refs()
        except KeyError:
            pass
        last_cell_id = request.get("executed_cell_id", self.last_executed_cell_id)
        cell_metadata_by_id = request.get(
            "cell_metadata_by_id", self._prev_cell_metadata_by_id
        )
        if cell_metadata_by_id is None:
            # bail if we don't have this
            null_vals = []
            if cell_metadata_by_id is None:
                null_vals.append("cell_metadata_by_id")
            return {"success": False, "error": f"null value for {', '.join(null_vals)}"}
        cells_to_check = list(
            cell
            for cell in (
                cells().from_id_nullable(cell_id) for cell_id in cell_metadata_by_id
            )
            if cell is not None
        )
        exec_schedule = self.mut_settings.exec_schedule
        response = self.check_and_link_multiple_cells(
            cells_to_check=cells_to_check,
            last_executed_cell_id=last_cell_id,
            allow_new_ready=request.get(
                "allow_new_ready",
                allow_new_ready
                and exec_schedule
                in (
                    ExecutionSchedule.LIVENESS_BASED,
                    ExecutionSchedule.HYBRID_DAG_LIVENESS_BASED,
                ),
            ),
        ).to_json()
        response["type"] = "compute_exec_schedule"
        response["exec_mode"] = self.mut_settings.exec_mode.value
        response["exec_schedule"] = exec_schedule.value
        response["flow_order"] = self.mut_settings.flow_order.value
        response["last_executed_cell_id"] = last_cell_id
        response["highlights"] = self.mut_settings.highlights.value
        response["last_execution_was_error"] = (
            self._exception_raised_during_execution is not None
        )
        response["is_reactively_executing"] = is_reactively_executing
        response["settings"] = dict(
            self.mut_settings.to_json().items() | self.settings.to_json().items()
        )
        cell_parents: Dict[IdType, List[IdType]] = {}
        cell_children: Dict[IdType, List[IdType]] = {}
        for cell in cells_to_check:
            this_cell_parents: Set[IdType] = set()
            latest_par_by_ts = cell.get_latest_parent_by_ts_map()
            for _ in self.mut_settings.iter_slicing_contexts():
                for par_id, raw_syms in cell.directional_parents.items():
                    syms = raw_syms - cell.static_removed_symbols
                    if len(syms) == 0:
                        continue
                    if (
                        latest_par_by_ts is not None
                        and self.fake_edge_sym not in syms
                        and par_id
                        not in {
                            latest_par_by_ts[sym.shallow_timestamp].cell_id
                            for sym in syms
                        }
                    ):
                        continue
                    parent = cells().from_id(par_id)
                    if (
                        parent.last_check_result is not None
                        and syms <= parent.static_writes
                        and len(parent.last_check_result.modified & syms) == 0
                    ):
                        continue
                    if parent.cell_ctr >= 0 and not any(
                        parent.cell_ctr
                        in {ts.cell_num for ts in sym.updated_timestamps}
                        for sym in syms
                    ):
                        continue
                    should_skip = False
                    if (
                        self.mut_settings.flow_order == FlowDirection.IN_ORDER
                        and slicing_ctx_var.get() == SlicingContext.STATIC
                    ):
                        for (
                            other_par_id,
                            other_syms,
                        ) in cell.directional_parents.items():
                            if syms == {self.fake_edge_sym} and other_syms == {
                                self.fake_edge_sym
                            }:
                                continue
                            other_parent = cells().from_id(other_par_id)
                            if other_parent.position <= parent.position:
                                continue
                            if syms <= other_syms or self.fake_edge_sym in other_syms:
                                should_skip = True
                                break
                    if should_skip:
                        continue
                    this_cell_parents.add(par_id)
            cell_parents[cell.id] = list(this_cell_parents)
        for cell_id, parents in cell_parents.items():
            for parent_id in parents:
                cell_children.setdefault(parent_id, []).append(cell_id)
        cell_children = {k: list(set(v)) for k, v in cell_children.items()}
        for cell in cells_to_check:
            if cell.cell_id not in cell_children:
                cell_children[cell.cell_id] = []
        response["cell_parents"] = cell_parents
        response["cell_children"] = cell_children
        response["executed_cells"] = list(cells().all_executed_cell_ids())
        return response

    def handle_compute_exec_schedule(
        self,
        request: Dict[str, Any],
        notify_content_changed: bool = True,
        allow_new_ready: bool = True,
    ) -> Optional[Dict[str, Any]]:
        try:
            return self._handle_compute_exec_schedule_impl(
                request,
                notify_content_changed=notify_content_changed,
                allow_new_ready=allow_new_ready,
            )
        finally:
            self.active_cell_id = None

    def handle_reactivity_cleanup(self, _request=None) -> None:
        self.min_cascading_reactive_cell_num = self.cell_counter()
        self._min_new_ready_cell_counter = self.cell_counter() + 1
        self.updated_reactive_symbols.clear()
        self.updated_deep_reactive_symbols.clear()

    def toggle_reactivity(self):
        if self.mut_settings.exec_mode == ExecutionMode.NORMAL:
            self.mut_settings.exec_mode = ExecutionMode.REACTIVE
        elif self.mut_settings.exec_mode == ExecutionMode.REACTIVE:
            self.mut_settings.exec_mode = ExecutionMode.NORMAL
        else:
            raise ValueError("unhandled exec mode: %s" % self.mut_settings.exec_mode)
        self._min_new_ready_cell_counter = self.cell_counter() + 1

    def handle_refresh_symbols(self, request) -> None:
        for symbol_str in request.get("symbols", []):
            sym = SymbolRef.resolve(symbol_str)
            if sym is not None:
                sym.refresh(take_timestamp_snapshots=False)
        return None

    def handle_upsert_symbol(self, request) -> Optional[Dict[str, Any]]:
        symbol_name = request["symbol"]
        user_globals = shell().user_global_ns
        if symbol_name not in user_globals:
            return {"success": False}
        dep_symbols = set()
        for dep in request.get("deps", []):
            dep_sym = SymbolRef.resolve(dep)
            if dep_sym is not None:
                dep_symbols.add(dep_sym)
        obj = user_globals.get(symbol_name)
        prev_sym = self.global_scope.get(symbol_name)
        if prev_sym is not None and prev_sym.obj is obj:
            return {"success": False}
        with Timestamp.offset(stmt_offset=-1):
            self.global_scope.upsert_symbol_for_name(
                symbol_name, obj, dep_symbols, ast.parse("pass").body[0]
            )
        return None

    def handle_get_code(self, request) -> Dict[str, Any]:
        symbol_name = request["symbol"]
        sym = self.global_scope.get(symbol_name)
        if sym is None:
            return {"success": False}
        return {
            "symbol": symbol_name,
            "code": str(sym.code(format_type=str)),
        }

    def handle_get_last_updated_cell_id(self, request) -> Dict[str, Any]:
        symbol_name = request["symbol"]
        sym = self.global_scope.get(symbol_name)
        if sym is None:
            return {"success": False}
        try:
            last_updated_cell_id = cells().at_timestamp(sym.timestamp).id
        except KeyError:
            return {"success": False}
        return {
            "symbol": symbol_name,
            "cell_id": last_updated_cell_id,
        }

    def handle_bump_timestamp(self, request) -> None:
        timestamp_name = request["timestamp"]
        self.tracked_timestamps[timestamp_name] = Timestamp.current()
        return None

    def handle_register_dynamic_comm_handler(self, request) -> Optional[Dict[str, Any]]:
        handler_msg_type = request.get("msg_type", None)
        handler_str = request.get("handler", None)
        if handler_msg_type is None or handler_str is None:
            return None
        handler_str = handler_str.strip()
        handler_str = textwrap.indent(textwrap.dedent(handler_str).strip(), " " * 4)
        handler_fun_name = f"_X5ix_{handler_msg_type}_handler"
        handler_str = f"def {handler_fun_name}(self, request):\n{handler_str}"
        exec(handler_str, globals())
        handler = globals().pop(handler_fun_name, None)
        self.register_comm_handler(
            handler_msg_type,
            lambda request_: handler(self, request_),
            overwrite=request.get("overwrite", False),
        )
        return None

    def check_and_link_multiple_cells(
        self,
        cells_to_check: Optional[Iterable[Cell]] = None,
        update_liveness_time_versions: bool = False,
        last_executed_cell_id: Optional[IdType] = None,
        clear_updated_reactive_symbols: bool = False,
        allow_new_ready: bool = True,
    ) -> FrontendCheckerResult:
        result = FrontendCheckerResult.empty(allow_new_ready=allow_new_ready)
        try:
            if (
                DataflowTracer not in singletons.shell().registered_tracers
                or not DataflowTracer.initialized()
            ):
                return result
            return result.compute_frontend_checker_result(
                cells_to_check=cells_to_check,
                update_liveness_time_versions=update_liveness_time_versions,
                last_executed_cell_id=last_executed_cell_id,
            )
        finally:
            if clear_updated_reactive_symbols:
                self.updated_reactive_symbols_last_cell.clear()
                self.updated_deep_reactive_symbols_last_cell.clear()

    def _safety_precheck_cell(self, cell: Cell) -> None:
        for tracer in singletons.shell().registered_tracers:
            # just make sure all tracers are initialized
            tracer.instance()
        checker_result = self.check_and_link_multiple_cells(
            cells_to_check=[cell],
            update_liveness_time_versions=self.mut_settings.static_slicing_enabled,
            clear_updated_reactive_symbols=True,
        )
        if cell.cell_id in checker_result.waiting_cells:
            self.waiter_usage_detected = True
        unsafe_order_cells = checker_result.unsafe_order_cells.get(cell.cell_id, None)
        if unsafe_order_cells is not None:
            self.out_of_order_usage_detected_counter = max(
                (cell.position, cell.cell_ctr) for cell in unsafe_order_cells
            )[1]

    def _add_parents_for_override_live_refs(self) -> None:
        for live_sym_ref in cells().current_cell().override_live_refs or []:
            sym = SymbolRef.resolve(live_sym_ref)
            if sym is not None:
                with static_slicing_context():
                    self.add_data_dep(
                        Timestamp(self.cell_counter(), 0), sym.timestamp, sym
                    )

    def _resync_symbols(self, symbols: Iterable[Symbol]):
        for sym in symbols:
            sym.resync_if_necessary(refresh=False)

    def _remove_dangling_parent_edges(self, dangling: Set[Symbol]) -> None:
        for _ in SlicingContext.iter_slicing_contexts():
            for cell in cells().iterate_over_notebook_in_counter_order():
                for pid in list(cell.raw_parents.keys()):
                    cell.remove_parent_edges(pid, dangling)
        cell = cells().at_counter(self.cell_counter())
        prev_cell = cell.prev_cell
        if prev_cell is None:
            return
        for _ in SlicingContext.iter_slicing_contexts():
            for prev_pid, sym_edges in list(prev_cell.raw_parents.items()):
                # remove anything not in the current parent set
                cell.remove_parent_edges(
                    prev_pid, sym_edges - cell.raw_parents.get(prev_pid, set())
                )

    @property
    def line_magic_name(self):
        return self._line_magic.__name__

    def all_symbols(self) -> Iterable[Symbol]:
        for alias_set in self.aliases.values():
            yield from alias_set

    def test_and_clear_waiter_usage_detected(self):
        ret = self.waiter_usage_detected
        self.waiter_usage_detected = False
        return ret

    def test_and_clear_out_of_order_usage_detected_counter(self):
        ret = self.out_of_order_usage_detected_counter
        self.out_of_order_usage_detected_counter = None
        return ret

    def gc(self):
        # Need to do the garbage marking and the collection separately
        prev_cell = cells().at_counter(self.cell_counter()).prev_cell
        prev_cell_ctr = -1 if prev_cell is None else prev_cell.cell_ctr
        if prev_cell_ctr > 0:
            for sym in self.all_symbols():
                if sym.defined_cell_num != prev_cell_ctr:
                    continue
                if sym.is_anonymous or sym.is_new_garbage():
                    sym.mark_garbage()
        garbage_syms = [sym for sym in self.all_symbols() if sym.is_garbage]
        for sym in garbage_syms:
            sym.collect_self_garbage()
        garbage_namespaces = [ns for ns in self.namespaces.values() if ns.is_garbage]
        for ns in garbage_namespaces:
            if ns.size == 0:
                ns.collect_self_garbage()
            else:
                ns.unmark_garbage()

    def retrieve_namespace_attr_or_sub(
        self, obj: Any, attr_or_sub: SupportedIndexType, is_subscript: bool
    ):
        try:
            with pyc.allow_reentrant_event_handling():
                if is_subscript:
                    # TODO: more complete list of things that are checkable
                    #  or could cause side effects upon subscripting
                    if type(obj) is defaultdict:
                        raise TypeError("subscript on defaultdict not allowed")
                    return obj[attr_or_sub]
                else:
                    if self.is_dev_mode:
                        assert isinstance(attr_or_sub, str)
                    return getattr(obj, cast(str, attr_or_sub))
        except (AttributeError, IndexError, KeyError, TypeError):
            raise
        except Exception as e:
            if self.is_dev_mode:
                logger.warning("unexpected exception: %s", e)
                logger.warning("object: %s", obj)
                logger.warning("attr / subscript: %s", attr_or_sub)
            raise e
