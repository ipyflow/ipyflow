# -*- coding: utf-8 -*-
from collections import defaultdict
from dataclasses import dataclass
import logging
from types import FrameType
from typing import (
    cast,
    Any,
    Dict,
    Iterable,
    NamedTuple,
    Set,
    Optional,
    Tuple,
)

import pyccolo as pyc
from IPython import get_ipython

from nbsafety.data_model.code_cell import cells, ExecutedCodeCell
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.data_model.namespace import Namespace
from nbsafety.data_model.scope import Scope
from nbsafety.data_model.timestamp import Timestamp
from nbsafety.frontend import FrontendCheckerResult
from nbsafety.line_magics import make_line_magic
from nbsafety.run_mode import ExecutionMode, ExecutionSchedule, FlowOrder, SafetyRunMode
from nbsafety import singletons
from nbsafety.tracing.nbsafety_tracer import SafetyTracer
from nbsafety.types import CellId, SupportedIndexType

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class NotebookSafetySettings(NamedTuple):
    test_context: bool
    use_comm: bool
    mark_stale_symbol_usages_unsafe: bool
    mark_typecheck_failures_unsafe: bool
    mark_phantom_cell_usages_unsafe: bool
    mode: SafetyRunMode


@dataclass
class MutableNotebookSafetySettings:
    trace_messages_enabled: bool
    highlights_enabled: bool
    static_slicing_enabled: bool
    dynamic_slicing_enabled: bool
    exec_mode: ExecutionMode
    exec_schedule: ExecutionSchedule
    flow_order: FlowOrder


class NotebookSafety(singletons.NotebookSafety):
    """Holds all the state necessary to detect stale dependencies in Jupyter notebooks."""

    def __init__(
        self,
        cell_magic_name=None,
        use_comm=False,
        **kwargs,
    ):
        super().__init__()
        cells().clear()
        self.settings: NotebookSafetySettings = NotebookSafetySettings(
            test_context=kwargs.pop("test_context", False),
            use_comm=use_comm,
            mark_stale_symbol_usages_unsafe=kwargs.pop(
                "mark_stale_symbol_usages_unsafe", True
            ),
            mark_typecheck_failures_unsafe=kwargs.pop(
                "mark_typecheck_failures_unsafe", False
            ),
            mark_phantom_cell_usages_unsafe=kwargs.pop(
                "mark_phantom_cell_usages_unsafe", False
            ),
            mode=SafetyRunMode.get(),
        )
        self.mut_settings: MutableNotebookSafetySettings = (
            MutableNotebookSafetySettings(
                trace_messages_enabled=kwargs.pop("trace_messages_enabled", False),
                highlights_enabled=kwargs.pop("highlights_enabled", True),
                static_slicing_enabled=kwargs.pop("static_slicing_enabled", True),
                dynamic_slicing_enabled=kwargs.pop("dynamic_slicing_enabled", True),
                exec_mode=ExecutionMode(kwargs.pop("exec_mode", ExecutionMode.NORMAL)),
                exec_schedule=ExecutionSchedule(
                    kwargs.pop("exec_schedule", ExecutionSchedule.LIVENESS_BASED)
                ),
                flow_order=FlowOrder(kwargs.pop("flow_order", FlowOrder.ANY_ORDER)),
            )
        )
        # Note: explicitly adding the types helps PyCharm intellisense
        self.namespaces: Dict[int, Namespace] = {}
        # TODO: wrap this in something that clears the dict entry when the set is 0 length
        self.aliases: Dict[int, Set[DataSymbol]] = defaultdict(set)
        self.dynamic_data_deps: Dict[Timestamp, Set[Timestamp]] = defaultdict(set)
        self.static_data_deps: Dict[Timestamp, Set[Timestamp]] = defaultdict(set)
        self.global_scope: Scope = Scope()
        self.updated_symbols: Set[DataSymbol] = set()
        self.updated_reactive_symbols: Set[DataSymbol] = set()
        self.updated_deep_reactive_symbols: Set[DataSymbol] = set()
        self.blocked_reactive_timestamps_by_symbol: Dict[DataSymbol, int] = {}
        self.statement_to_func_cell: Dict[int, DataSymbol] = {}
        self._active_cell_id: Optional[CellId] = None
        self.stale_usage_detected = False
        self.out_of_order_usage_detected_counter: Optional[int] = None
        if cell_magic_name is None:
            self._cell_magic = None
        else:
            self._cell_magic = singletons.kernel().make_cell_magic(cell_magic_name)
        self._line_magic = make_line_magic(self)
        self._prev_cell_stale_symbols: Set[DataSymbol] = set()
        self._cell_name_to_cell_num_mapping: Dict[str, int] = {}
        self._exception_raised_during_execution: Optional[Exception] = None
        self._saved_debug_message: Optional[str] = None
        self.min_timestamp = -1
        self._tags: Tuple[str, ...] = ()
        self.last_executed_content: Optional[str] = None
        self.last_executed_cell_id: Optional[CellId] = None
        if use_comm:
            get_ipython().kernel.comm_manager.register_target(
                __package__, self._comm_target
            )

    @property
    def is_develop(self) -> bool:
        return self.settings.mode == SafetyRunMode.DEVELOP

    @property
    def is_test(self) -> bool:
        return self.settings.test_context

    @property
    def trace_messages_enabled(self) -> bool:
        return self.mut_settings.trace_messages_enabled

    @trace_messages_enabled.setter
    def trace_messages_enabled(self, new_val) -> None:
        self.mut_settings.trace_messages_enabled = new_val

    def get_first_full_symbol(self, obj_id: int) -> Optional[DataSymbol]:
        # TODO: also avoid anonymous namespaces?
        for alias in self.aliases.get(obj_id, []):
            if not alias.is_anonymous:
                return alias
        return None

    @staticmethod
    def cell_counter() -> int:
        return cells().exec_counter()

    def add_dynamic_data_dep(self, child: Timestamp, parent: Timestamp):
        self.dynamic_data_deps[child].add(parent)
        cells().from_timestamp(child).add_dynamic_parent(cells().from_timestamp(parent))

    def add_static_data_dep(self, child: Timestamp, parent: Timestamp):
        self.static_data_deps[child].add(parent)
        cells().from_timestamp(child).add_static_parent(cells().from_timestamp(parent))

    def reset_cell_counter(self):
        # only called in test context
        assert not singletons.kernel().settings.store_history
        self.dynamic_data_deps.clear()
        self.static_data_deps.clear()
        for sym in self.all_data_symbols():
            sym._timestamp = (
                sym._max_inner_timestamp
            ) = sym.required_timestamp = Timestamp.uninitialized()
            sym.timestamp_by_used_time.clear()
            sym.timestamp_by_liveness_time.clear()
        cells().clear()

    def set_exception_raised_during_execution(
        self, new_val: Optional[Exception] = None
    ) -> Optional[Exception]:
        ret = self._exception_raised_during_execution
        self._exception_raised_during_execution = new_val
        return ret

    def get_position(self, frame: FrameType) -> Tuple[Optional[int], int]:
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

    def set_active_cell(self, cell_id: CellId) -> None:
        self._active_cell_id = cell_id

    def set_tags(self, tags: Tuple[str, ...]) -> None:
        self._tags = tags

    def reactivity_cleanup(self) -> None:
        for cell in cells().all_cells_most_recently_run_for_each_id():
            cell.set_fresh(False)

    def _comm_target(self, comm, open_msg) -> None:
        @comm.on_msg
        def _responder(msg):
            request = msg["content"]["data"]
            self.handle(request, comm=comm)

        comm.send({"type": "establish"})

    def handle(self, request, comm=None) -> None:
        if request["type"] == "change_active_cell":
            self.set_active_cell(request["active_cell_id"])
        elif request["type"] == "cell_freshness":
            if self._active_cell_id is None:
                self.set_active_cell(request.get("executed_cell_id", None))
            cell_id = request.get("executed_cell_id", None)
            order_index_by_id = request["order_index_by_cell_id"]
            cells().set_cell_positions(order_index_by_id)
            cells_to_check = (
                cell
                for cell in (cells().from_id(cell_id) for cell_id in order_index_by_id)
                if cell is not None
            )
            response = self.check_and_link_multiple_cells(
                cells_to_check=cells_to_check, last_executed_cell_id=cell_id
            ).to_json()
            response["type"] = "cell_freshness"
            response["exec_mode"] = self.mut_settings.exec_mode.value
            response["exec_schedule"] = self.mut_settings.exec_schedule.value
            response["flow_order"] = self.mut_settings.flow_order.value
            response["last_executed_cell_id"] = cell_id
            response["highlights_enabled"] = self.mut_settings.highlights_enabled
            if comm is not None:
                comm.send(response)
        elif request["type"] == "reactivity_cleanup":
            self.reactivity_cleanup()
        else:
            dbg_msg = "Unsupported request type for request %s" % request
            logger.error(dbg_msg)
            self._saved_debug_message = dbg_msg

    def check_and_link_multiple_cells(
        self,
        cells_to_check: Optional[Iterable[ExecutedCodeCell]] = None,
        update_liveness_time_versions: bool = False,
        last_executed_cell_id: Optional[CellId] = None,
    ) -> FrontendCheckerResult:
        result = FrontendCheckerResult.empty()
        if SafetyTracer not in singletons.kernel().registered_tracers:
            return result
        for tracer in singletons.kernel().registered_tracers:
            # force initialization here in case not already inited
            tracer.instance()
        return result.compute_frontend_checker_result(
            cells_to_check=cells_to_check,
            update_liveness_time_versions=update_liveness_time_versions,
            last_executed_cell_id=last_executed_cell_id,
        )

    def _safety_precheck_cell(self, cell: ExecutedCodeCell) -> None:
        checker_result = self.check_and_link_multiple_cells(
            cells_to_check=[cell],
            update_liveness_time_versions=self.mut_settings.static_slicing_enabled,
        )
        if cell.cell_id in checker_result.stale_cells:
            self.stale_usage_detected = True
        unsafe_order_cells = checker_result.unsafe_order_cells.get(cell.cell_id, None)
        if unsafe_order_cells is not None:
            self.out_of_order_usage_detected_counter = max(
                (cell.position, cell.cell_ctr) for cell in unsafe_order_cells
            )[1]

    def _resync_symbols(self, symbols: Iterable[DataSymbol]):
        for dsym in symbols:
            if not dsym.containing_scope.is_global:
                continue
            obj = get_ipython().user_global_ns.get(dsym.name, None)
            if obj is None:
                continue
            if dsym.obj_id == id(obj):
                continue
            for alias in self.aliases[dsym.cached_obj_id] | self.aliases[dsym.obj_id]:
                containing_namespace = alias.containing_namespace
                if containing_namespace is None:
                    continue
                containing_obj = containing_namespace.obj
                if containing_obj is None:
                    continue
                # TODO: handle dict case too
                if isinstance(containing_obj, list) and containing_obj[-1] is obj:
                    containing_namespace._subscript_data_symbol_by_name.pop(
                        alias.name, None
                    )
                    alias.name = len(containing_obj) - 1
                    alias.update_obj_ref(obj)
                    containing_namespace._subscript_data_symbol_by_name[
                        alias.name
                    ] = alias
            self.aliases[dsym.cached_obj_id].discard(dsym)
            self.aliases[dsym.obj_id].discard(dsym)
            self.aliases[id(obj)].add(dsym)
            dsym.update_obj_ref(obj)

    @property
    def cell_magic_name(self):
        return self._cell_magic.__name__

    @property
    def line_magic_name(self):
        return self._line_magic.__name__

    def all_data_symbols(self) -> Iterable[DataSymbol]:
        for alias_set in self.aliases.values():
            yield from alias_set

    def test_and_clear_stale_usage_detected(self):
        ret = self.stale_usage_detected
        self.stale_usage_detected = False
        return ret

    def test_and_clear_out_of_order_usage_detected_counter(self):
        ret = self.out_of_order_usage_detected_counter
        self.out_of_order_usage_detected_counter = None
        return ret

    def gc(self):
        # Need to do the garbage check and the collection separately
        garbage_syms = [
            dsym for dsym in self.all_data_symbols() if dsym.is_new_garbage()
        ]
        for dsym in garbage_syms:
            dsym.collect_self_garbage()

    def retrieve_namespace_attr_or_sub(
        self, obj: Any, attr_or_sub: SupportedIndexType, is_subscript: bool
    ):
        try:
            with pyc.allow_reentrant_event_handling():
                if is_subscript:
                    # TODO: more complete list of things that are checkable
                    #  or could cause side effects upon subscripting
                    return obj[attr_or_sub]
                else:
                    if self.is_develop:
                        assert isinstance(attr_or_sub, str)
                    return getattr(obj, cast(str, attr_or_sub))
        except (AttributeError, IndexError, KeyError):
            raise
        except Exception as e:
            if self.is_develop:
                logger.warning("unexpected exception: %s", e)
                logger.warning("object: %s", obj)
                logger.warning("attr / subscript: %s", attr_or_sub)
            raise e
