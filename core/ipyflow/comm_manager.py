# -*- coding: utf-8 -*-
import ast
import logging
import textwrap
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Set

import pyccolo as pyc
from ipykernel.comm import Comm
from ipykernel.ipkernel import IPythonKernel

from ipyflow.analysis.resolved_symbols import ResolvedSymbol
from ipyflow.analysis.symbol_ref import SymbolRef
from ipyflow.config import ExecutionSchedule
from ipyflow.data_model.cell import cells
from ipyflow.data_model.symbol import Symbol
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.singletons import shell
from ipyflow.types import IdType

if TYPE_CHECKING:
    from ipyflow.flow import NotebookFlow


logger = logging.getLogger(__name__)


class CommManager:
    """Manages communication between the IPyflow backend and frontend."""

    NO_RESPONSE = object()

    def __init__(self, flow_instance: "NotebookFlow") -> None:
        # Keep a reference to the flow instance to access its methods and state
        self.flow = flow_instance
        self._comm_handlers: Dict[
            str, Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]
        ] = {}
        self._comm: Optional[Comm] = None
        self.debounced_exec_schedule_pending = False

        # Register default handlers
        self._register_default_handlers()

    def _register_default_handlers(self) -> None:
        """Register all the default comm handlers."""
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

    def register_comm_target(self, kernel: IPythonKernel) -> None:
        """Register the comm target with the kernel."""
        kernel.comm_manager.register_target(__package__, self._comm_target)

    def register_comm_handler(
        self,
        msg_type: str,
        handler: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
        overwrite: bool = False,
    ) -> None:
        """Register a handler for a specific message type."""
        if msg_type in self._comm_handlers and not overwrite:
            raise ValueError("handler already registered for msg type of %s" % msg_type)
        self._comm_handlers[msg_type] = handler

    def handle(self, request: Dict[str, Any], comm=None) -> None:
        """Handle a comm request by dispatching to the appropriate handler."""
        request_type = request["type"]
        handler = self._comm_handlers.get(request_type)
        if handler is None:
            dbg_msg = "Unsupported request type for request %s" % request
            logger.error(dbg_msg)
            self.flow._saved_debug_message = dbg_msg
            return
        try:
            response = handler(request)
        except Exception as e:
            response = {
                "success": False,
                "error": str(e),
            }
            if self.flow.is_dev_mode:
                logger.exception("exception during comm handler execution")
        if response is self.NO_RESPONSE:
            return
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

    def _comm_target(self, comm: Comm, open_msg: Dict[str, Any]) -> None:
        """Handle comm target initialization."""

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
        self.flow.initialize(**open_msg.get("content", {}).get("data", {}))
        comm.send({"type": "establish", "success": True})

    def handle_change_active_cell(
        self, request: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Handle change active cell request."""
        self.flow.set_active_cell(request["active_cell_id"])
        return None

    def _handle_compute_exec_schedule_impl(
        self,
        request: Dict[str, Any],
        notify_content_changed: bool = True,
        allow_new_ready: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if not self.flow.mut_settings.dataflow_enabled:
            return {"success": False, "error": "dataflow not enabled"}
        is_reactively_executing = request.get("is_reactively_executing", False)
        if self.flow.active_cell_id is None:
            active_cell_id = request.get("active_cell_id")
            if active_cell_id is not None:
                self.flow.set_active_cell(active_cell_id)
            if self.flow.active_cell_id is not None:
                prev_cell = cells().current_cell()
                if prev_cell.is_placeholder_id:
                    prev_cell.update_id(self.flow.active_cell_id)
        if notify_content_changed and request.get("notify_content_changed", True):
            self._handle_notify_content_changed_impl(
                request, is_reactively_executing=is_reactively_executing
            )
        try:
            self.flow._add_parents_for_override_live_refs()
        except KeyError:
            pass
        last_cell_id = request.get("executed_cell_id", self.flow.last_executed_cell_id)
        cell_metadata_by_id = request.get(
            "cell_metadata_by_id", self.flow._prev_cell_metadata_by_id
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
        exec_schedule = self.flow.mut_settings.exec_schedule
        response = self.flow.check_and_link_multiple_cells(
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
        response["exec_mode"] = self.flow.mut_settings.exec_mode.value
        response["exec_schedule"] = exec_schedule.value
        response["flow_order"] = self.flow.mut_settings.flow_order.value
        response["last_executed_cell_id"] = last_cell_id
        response["highlights"] = self.flow.mut_settings.highlights.value
        response["last_execution_was_error"] = (
            self.flow._exception_raised_during_execution is not None
        )
        response["is_reactively_executing"] = is_reactively_executing
        response["settings"] = dict(
            self.flow.mut_settings.to_json().items()
            | self.flow.settings.to_json().items()
        )
        response["executed_cells"] = list(cells().all_executed_cell_ids())
        return response

    def handle_compute_exec_schedule(
        self,
        request: Dict[str, Any],
        notify_content_changed: bool = True,
        allow_new_ready: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Handle compute execution schedule request."""
        try:
            return self._handle_compute_exec_schedule_impl(
                request,
                notify_content_changed=notify_content_changed,
                allow_new_ready=allow_new_ready,
            )
        finally:
            self.flow.active_cell_id = None

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

    def _handle_notify_content_changed_impl(
        self, request: Dict[str, Any], is_reactively_executing: bool = False
    ) -> Optional[Dict[str, Any]]:
        cell_metadata_by_id = request.get(
            "cell_metadata_by_id", self.flow._prev_cell_metadata_by_id
        )
        if cell_metadata_by_id is None:
            # bail if we don't have this
            return {"success": False, "error": "null value for cell metadata"}
        is_cell_structure_change = self.flow._prev_cell_metadata_by_id is None or len(
            self.flow._prev_cell_metadata_by_id
        ) != len(cell_metadata_by_id)
        self.flow._prev_cell_metadata_by_id = cell_metadata_by_id
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
            is_cell_structure_change
            or self.flow._prev_order_idx_by_id != order_index_by_id
        )
        prev_order_idx_by_id = self.flow._prev_order_idx_by_id
        self.flow._prev_order_idx_by_id = order_index_by_id
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
        self.flow._create_untracked_cells_for_content(content_by_cell_id)
        if should_recompute_exec_schedule:
            return self.handle_compute_exec_schedule(
                request, notify_content_changed=False, allow_new_ready=False
            )
        else:
            return None

    def handle_notify_content_changed(
        self, request: Dict[str, Any], is_reactively_executing: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Handle notify content changed request."""
        return self._handle_notify_content_changed_impl(
            request, is_reactively_executing=is_reactively_executing
        )

    def handle_reactivity_cleanup(self, _request=None) -> None:
        """Handle reactivity cleanup request."""
        self.flow.min_cascading_reactive_cell_num = self.flow.cell_counter()
        self.flow._min_new_ready_cell_counter = self.flow.cell_counter() + 1
        self.flow.updated_reactive_symbols.clear()
        self.flow.updated_deep_reactive_symbols.clear()

    def handle_refresh_symbols(self, request) -> None:
        """Handle refresh symbols request."""
        for symbol_str in request.get("symbols", []):
            sym = SymbolRef.resolve(symbol_str)
            if sym is not None:
                sym.refresh(take_timestamp_snapshots=False)
        return None

    def handle_upsert_symbol(self, request) -> Optional[Dict[str, Any]]:
        """Handle upsert symbol request."""
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
        prev_sym = self.flow.global_scope.get(symbol_name)
        if prev_sym is not None and prev_sym.obj is obj:
            return {"success": False}
        with Timestamp.offset(stmt_offset=-1):
            self.flow.global_scope.upsert_symbol_for_name(
                symbol_name, obj, dep_symbols, ast.parse("pass").body[0]
            )
        return None

    def handle_get_code(self, request) -> Dict[str, Any]:
        """Handle get code request."""
        symbol_name = request["symbol"]
        sym = self.flow.global_scope.get(symbol_name)
        if sym is None:
            return {"success": False}
        return {
            "symbol": symbol_name,
            "code": str(sym.code(format_type=str)),
        }

    def handle_get_last_updated_cell_id(self, request) -> Dict[str, Any]:
        """Handle get last updated cell ID request."""
        symbol_name = request["symbol"]
        sym = self.flow.global_scope.get(symbol_name)
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
        """Handle bump timestamp request."""
        timestamp_name = request["timestamp"]
        self.flow.tracked_timestamps[timestamp_name] = Timestamp.current()
        return None

    def handle_register_dynamic_comm_handler(self, request) -> Optional[Dict[str, Any]]:
        """Handle register dynamic comm handler request."""
        handler_msg_type = request.get("msg_type", None)
        handler_str = request.get("handler", None)
        if handler_msg_type is None or handler_str is None:
            return None
        handler_str = handler_str.strip()
        handler_str = textwrap.indent(textwrap.dedent(handler_str).strip(), " " * 4)
        handler_fun_name = f"{pyc.PYCCOLO_BUILTIN_PREFIX}_{handler_msg_type}_handler"
        handler_str = f"def {handler_fun_name}(self, request):\n{handler_str}"
        exec(handler_str, globals())
        handler = globals().pop(handler_fun_name, None)
        self.register_comm_handler(
            handler_msg_type,
            lambda request_: handler(self, request_),
            overwrite=request.get("overwrite", False),
        )
        return None
