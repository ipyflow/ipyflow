# -*- coding: utf-8 -*-
import asyncio
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
import inspect
import json
import logging
import re
from types import FrameType
from typing import (
    cast,
    Any,
    Callable,
    Dict,
    Generator,
    Iterable,
    List,
    NamedTuple,
    Set,
    Optional,
    Tuple,
    Union,
)

from IPython import get_ipython
from IPython.core.magic import register_cell_magic, register_line_magic

import pyccolo as pyc

from nbsafety.ipython_utils import (
    ast_transformer_context,
    input_transformer_context,
    run_cell,
    save_number_of_currently_executing_cell,
)
from nbsafety import line_magics
from nbsafety.data_model.code_cell import cells, ExecutedCodeCell
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.data_model.namespace import Namespace
from nbsafety.data_model.scope import Scope
from nbsafety.data_model.timestamp import Timestamp
from nbsafety.run_mode import ExecutionMode, ExecutionSchedule, FlowOrder, SafetyRunMode
from nbsafety import singletons
from nbsafety.tracing.nbsafety_tracer import (
    ModuleIniter,
    SafetyTracer,
    StackFrameManager,
)
from nbsafety.types import CellId, SupportedIndexType

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

_SAFETY_LINE_MAGIC = "safety"
_NB_MAGIC_PATTERN = re.compile(r"(^%|^!|^cd |\?$)")


class NotebookSafetySettings(NamedTuple):
    store_history: bool
    test_context: bool
    use_comm: bool
    track_dependencies: bool
    mark_stale_symbol_usages_unsafe: bool
    mark_typecheck_failures_unsafe: bool
    mark_phantom_cell_usages_unsafe: bool
    enable_reactive_modifiers: bool
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


class FrontendCheckerResult(NamedTuple):
    stale_cells: Set[CellId]
    fresh_cells: Set[CellId]
    new_fresh_cells: Set[CellId]
    forced_reactive_cells: Set[CellId]
    stale_links: Dict[CellId, Set[CellId]]
    refresher_links: Dict[CellId, Set[CellId]]
    phantom_cell_info: Dict[CellId, Dict[CellId, Set[int]]]

    def to_json(self) -> Dict[str, Any]:
        return {
            "stale_cells": list(self.stale_cells),
            "fresh_cells": list(self.fresh_cells),
            "new_fresh_cells": list(self.new_fresh_cells),
            "forced_reactive_cells": list(self.forced_reactive_cells),
            "stale_links": {
                cell_id: list(linked_cell_ids)
                for cell_id, linked_cell_ids in self.stale_links.items()
            },
            "refresher_links": {
                cell_id: list(linked_cell_ids)
                for cell_id, linked_cell_ids in self.refresher_links.items()
            },
        }


class NotebookSafety(singletons.NotebookSafety):
    """Holds all the state necessary to detect stale dependencies in Jupyter notebooks."""

    def __init__(self, cell_magic_name=None, use_comm=False, settrace=None, **kwargs):
        super().__init__()
        cells().clear()
        self.settings: NotebookSafetySettings = NotebookSafetySettings(
            store_history=kwargs.pop("store_history", True),
            test_context=kwargs.pop("test_context", False),
            use_comm=use_comm,
            track_dependencies=True,
            mark_stale_symbol_usages_unsafe=kwargs.pop(
                "mark_stale_symbol_usages_unsafe", True
            ),
            mark_typecheck_failures_unsafe=kwargs.pop(
                "mark_typecheck_failures_unsafe", False
            ),
            mark_phantom_cell_usages_unsafe=kwargs.pop(
                "mark_phantom_cell_usages_unsafe", False
            ),
            enable_reactive_modifiers=kwargs.pop("enable_reactive_modifiers", True),
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
        self.registered_tracers: List[Type[pyc.BaseTracer]] = [SafetyTracer]
        self.tracer_cleanup_callbacks: List[Callable] = []
        self.tracer_cleanup_pending: bool = False
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
        self.safety_issue_detected = False
        if cell_magic_name is None:
            self._cell_magic = None
        else:
            self._cell_magic = self._make_cell_magic(cell_magic_name)
        self._line_magic = self._make_line_magic()
        self._prev_cell_stale_symbols: Set[DataSymbol] = set()
        self._cell_name_to_cell_num_mapping: Dict[str, int] = {}
        self._exception_raised_during_execution: Optional[Exception] = None
        self._saved_debug_message: Optional[str] = None
        self.min_timestamp = -1
        self._tags: Tuple[str, ...] = ()
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
        assert not self.settings.store_history
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
        SafetyTracer.instance()  # force initialization here in case not already inited
        stale_cells = set()
        unsafe_order_cells: Set[CellId] = set()
        typecheck_error_cells = set()
        fresh_cells = set()
        new_fresh_cells = set()
        forced_reactive_cells = set()
        stale_symbols_by_cell_id: Dict[CellId, Set[DataSymbol]] = {}
        killing_cell_ids_for_symbol: Dict[DataSymbol, Set[CellId]] = defaultdict(set)
        phantom_cell_info: Dict[CellId, Dict[CellId, Set[int]]] = {}
        checker_results_by_cid = {}
        if last_executed_cell_id is None:
            last_executed_cell = None
            last_executed_cell_pos = None
        else:
            last_executed_cell = cells().from_id(last_executed_cell_id)
            last_executed_cell_pos = last_executed_cell.position
            for tag in last_executed_cell.tags:
                for reactive_cell_id in cells().get_reactive_ids_for_tag(tag):
                    forced_reactive_cells.add(reactive_cell_id)
        if cells_to_check is None:
            cells_to_check = cells().all_cells_most_recently_run_for_each_id()
        cells_to_check = sorted(cells_to_check, key=lambda c: c.position)
        for cell in cells_to_check:
            try:
                checker_result = cell.check_and_resolve_symbols(
                    update_liveness_time_versions=update_liveness_time_versions
                )
            except SyntaxError:
                continue
            cell_id = cell.cell_id
            checker_results_by_cid[cell_id] = checker_result
            # if self.mut_settings.flow_order == FlowOrder.IN_ORDER:
            #     for live_sym in checker_result.live:
            #         if cells().from_timestamp(live_sym.timestamp).position > cell.position:
            #             unsafe_order_cells.add(cell_id)
            #             break
            if self.mut_settings.flow_order == FlowOrder.IN_ORDER:
                if (
                    last_executed_cell_pos is not None
                    and cell.position <= last_executed_cell_pos
                ):
                    continue
            if self.mut_settings.exec_schedule == ExecutionSchedule.LIVENESS_BASED:
                stale_symbols = {
                    sym.dsym
                    for sym in checker_result.live
                    if sym.is_stale_at_position(cell.position)
                }
            else:
                stale_symbols = set()
            if len(stale_symbols) > 0:
                stale_symbols_by_cell_id[cell_id] = stale_symbols
                stale_cells.add(cell_id)
            if not checker_result.typechecks:
                typecheck_error_cells.add(cell_id)
            for dead_sym in checker_result.dead:
                killing_cell_ids_for_symbol[dead_sym].add(cell_id)

            is_fresh = cell_id not in stale_cells
            if self.settings.mark_phantom_cell_usages_unsafe:
                phantom_cell_info_for_cell = cell.compute_phantom_cell_info(
                    checker_result.used_cells
                )
                if len(phantom_cell_info_for_cell) > 0:
                    phantom_cell_info[cell_id] = phantom_cell_info_for_cell
            if self.mut_settings.exec_schedule == ExecutionSchedule.DAG_BASED:
                is_fresh = False
                flow_order = self.mut_settings.flow_order
                if self.mut_settings.dynamic_slicing_enabled:
                    for par in cell.dynamic_parents:
                        if (
                            flow_order == flow_order.IN_ORDER
                            and par.position >= cell.position
                        ):
                            continue
                        if par.cell_ctr > max(cell.cell_ctr, self.min_timestamp):
                            is_fresh = True
                            break
                if not is_fresh and self.mut_settings.static_slicing_enabled:
                    for par in cell.static_parents:
                        if (
                            flow_order == flow_order.IN_ORDER
                            and par.position >= cell.position
                        ):
                            continue
                        if par.cell_ctr > max(cell.cell_ctr, self.min_timestamp):
                            is_fresh = True
                            break
            else:
                is_fresh = is_fresh and (
                    cell.get_max_used_live_symbol_cell_counter(checker_result.live)
                    > max(cell.cell_ctr, self.min_timestamp)
                )
            if self.mut_settings.exec_schedule == ExecutionSchedule.STRICT:
                for dead_sym in checker_result.dead:
                    if dead_sym.timestamp.cell_num > max(
                        cell.cell_ctr, self.min_timestamp
                    ):
                        is_fresh = True
            if is_fresh:
                fresh_cells.add(cell_id)
            if not cells().from_id(cell_id).set_fresh(is_fresh) and is_fresh:
                new_fresh_cells.add(cell_id)
            if is_fresh and self.mut_settings.exec_schedule == ExecutionSchedule.STRICT:
                break
        if self.mut_settings.exec_schedule == ExecutionSchedule.DAG_BASED:
            prev_stale_cells: Set[CellId] = set()
            while True:
                for cell in cells_to_check:
                    if cell.cell_id in stale_cells:
                        continue
                    if self.mut_settings.dynamic_slicing_enabled:
                        if cell.dynamic_parent_ids & (fresh_cells | stale_cells):
                            stale_cells.add(cell.cell_id)
                            continue
                    if self.mut_settings.static_slicing_enabled:
                        if cell.static_parent_ids & (fresh_cells | stale_cells):
                            stale_cells.add(cell.cell_id)
                if prev_stale_cells == stale_cells:
                    break
                prev_stale_cells = set(stale_cells)
            fresh_cells -= stale_cells
            new_fresh_cells -= stale_cells
            for cell_id in stale_cells:
                cells().from_id(cell_id).set_fresh(False)
        if self.mut_settings.exec_mode != ExecutionMode.REACTIVE:
            for cell_id in new_fresh_cells:
                if cell_id not in checker_results_by_cid:
                    continue
                cell = cells().from_id(cell_id)
                if cell.get_max_used_live_symbol_cell_counter(
                    checker_results_by_cid[cell_id].live, filter_to_reactive=True
                ) > max(cell.cell_ctr, self.min_timestamp):
                    forced_reactive_cells.add(cell_id)
        stale_links: Dict[CellId, Set[CellId]] = defaultdict(set)
        refresher_links: Dict[CellId, Set[CellId]] = defaultdict(set)
        eligible_refresher_for_dag = fresh_cells | stale_cells
        for stale_cell_id in stale_cells:
            refresher_cell_ids: Set[CellId] = set()
            if self.mut_settings.flow_order == ExecutionSchedule.DAG_BASED:
                if self.mut_settings.dynamic_slicing_enabled:
                    refresher_cell_ids |= (
                        cells().from_id(stale_cell_id).dynamic_parent_ids
                        & eligible_refresher_for_dag
                    )
                if self.mut_settings.static_slicing_enabled:
                    refresher_cell_ids |= (
                        cells().from_id(stale_cell_id).static_parent_ids
                        & eligible_refresher_for_dag
                    )
            else:
                stale_syms = stale_symbols_by_cell_id.get(stale_cell_id, set())
                refresher_cell_ids = refresher_cell_ids.union(
                    *(
                        killing_cell_ids_for_symbol[stale_sym]
                        for stale_sym in stale_syms
                    )
                )
            if self.mut_settings.flow_order == FlowOrder.IN_ORDER:
                refresher_cell_ids = {
                    cid
                    for cid in refresher_cell_ids
                    if cells().from_id(cid).position
                    < cells().from_id(stale_cell_id).position
                }
            if last_executed_cell_id is not None:
                refresher_cell_ids.discard(last_executed_cell_id)
            stale_links[stale_cell_id] = refresher_cell_ids
        stale_link_changes = True
        # transitive closer up until we hit non-stale refresher cells
        while stale_link_changes:
            stale_link_changes = False
            for stale_cell_id in stale_cells:
                new_stale_links = set(stale_links[stale_cell_id])
                original_length = len(new_stale_links)
                for refresher_cell_id in stale_links[stale_cell_id]:
                    if refresher_cell_id not in stale_cells:
                        continue
                    new_stale_links |= stale_links[refresher_cell_id]
                new_stale_links.discard(stale_cell_id)
                stale_link_changes = stale_link_changes or original_length != len(
                    new_stale_links
                )
                stale_links[stale_cell_id] = new_stale_links
        for stale_cell_id in stale_cells:
            stale_links[stale_cell_id] -= stale_cells
            for refresher_cell_id in stale_links[stale_cell_id]:
                refresher_links[refresher_cell_id].add(stale_cell_id)
        return FrontendCheckerResult(
            # TODO: we should probably have separate fields for stale vs non-typechecking cells,
            #  or at least change the name to a more general "unsafe_cells" or equivalent
            stale_cells=stale_cells | typecheck_error_cells | unsafe_order_cells,
            fresh_cells=fresh_cells,
            new_fresh_cells=new_fresh_cells,
            forced_reactive_cells=forced_reactive_cells,
            stale_links=stale_links,
            refresher_links=refresher_links,
            phantom_cell_info=phantom_cell_info,
        )

    @staticmethod
    def _get_max_timestamp_cell_num_for_symbols(
        deep_symbols: Set[DataSymbol], shallow_symbols: Set[DataSymbol]
    ) -> int:
        max_timestamp_cell_num = -1
        for dsym in deep_symbols:
            max_timestamp_cell_num = max(
                max_timestamp_cell_num, dsym.timestamp.cell_num
            )
        for dsym in shallow_symbols:
            max_timestamp_cell_num = max(
                max_timestamp_cell_num, dsym.timestamp_excluding_ns_descendents.cell_num
            )
        return max_timestamp_cell_num

    def _safety_precheck_cell(self, cell: ExecutedCodeCell) -> None:
        checker_result = self.check_and_link_multiple_cells(
            cells_to_check=[cell],
            update_liveness_time_versions=self.mut_settings.static_slicing_enabled,
        )
        if cell.cell_id in checker_result.stale_cells:
            self.safety_issue_detected = True

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

    def create_dag_metadata(
        self,
    ) -> Dict[int, Dict[str, Union[List[int], List[str], Dict[str, Dict[str, str]]]]]:
        cell_num_to_used_imports: Dict[int, Set[DataSymbol]] = defaultdict(set)
        cell_num_to_dynamic_inputs: Dict[int, Set[DataSymbol]] = defaultdict(set)
        cell_num_to_dynamic_outputs: Dict[int, Set[DataSymbol]] = defaultdict(set)
        cell_num_to_dynamic_cell_parents: Dict[int, Set[int]] = defaultdict(set)
        cell_num_to_dynamic_cell_children: Dict[int, Set[int]] = defaultdict(set)

        for sym in self.all_data_symbols():
            top_level_sym = sym.get_top_level()
            if (
                top_level_sym is None
                or not top_level_sym.is_globally_accessible
                or top_level_sym.is_anonymous
            ):
                # TODO: also skip lambdas
                continue
            for (
                used_time,
                sym_timestamp_when_used,
            ) in sym.timestamp_by_used_time.items():
                if top_level_sym.is_import:
                    cell_num_to_used_imports[used_time.cell_num].add(top_level_sym)
                elif used_time.cell_num != sym_timestamp_when_used.cell_num:
                    cell_num_to_dynamic_cell_parents[used_time.cell_num].add(
                        sym_timestamp_when_used.cell_num
                    )
                    cell_num_to_dynamic_cell_children[
                        sym_timestamp_when_used.cell_num
                    ].add(used_time.cell_num)
                    cell_num_to_dynamic_inputs[used_time.cell_num].add(top_level_sym)
                    cell_num_to_dynamic_outputs[sym_timestamp_when_used.cell_num].add(
                        top_level_sym
                    )
            if not top_level_sym.is_import:
                for updated_time in sym.updated_timestamps:
                    # TODO: distinguished between used / unused outputs?
                    cell_num_to_dynamic_outputs[updated_time.cell_num].add(
                        top_level_sym
                    )

        cell_metadata: Dict[
            int, Dict[str, Union[List[int], List[str], Dict[str, Dict[str, str]]]]
        ] = {}
        all_relevant_cells = (
            cell_num_to_used_imports.keys()
            | cell_num_to_dynamic_inputs.keys()
            | cell_num_to_dynamic_outputs.keys()
            | cell_num_to_dynamic_cell_parents.keys()
            | cell_num_to_dynamic_cell_children.keys()
        )
        for cell_num in all_relevant_cells:
            cell_imports = [
                dsym.get_import_string() for dsym in cell_num_to_used_imports[cell_num]
            ]
            input_symbols = {
                str(dsym): {"type": dsym.get_type_annotation_string()}
                for dsym in cell_num_to_dynamic_inputs[cell_num]
            }
            output_symbols = {
                str(dsym): {"type": dsym.get_type_annotation_string()}
                for dsym in cell_num_to_dynamic_outputs[cell_num]
            }
            parent_cells = list(cell_num_to_dynamic_cell_parents[cell_num])
            child_cells = list(cell_num_to_dynamic_cell_children[cell_num])
            cell_metadata[cell_num] = {
                "cell_imports": cell_imports,
                "input_symbols": input_symbols,
                "output_symbols": output_symbols,
                "parent_cells": parent_cells,
                "child_cells": child_cells,
            }
        return cell_metadata

    @contextmanager
    def _patch_pyccolo_exec_eval(self):
        """
        The purpose of this context manager is to disable this project's
        tracer inside pyccolo's "exec()" functions, since it probably
        will not work properly inside of these.
        """
        orig_exec = pyc.exec
        orig_eval = pyc.eval
        orig_tracer_exec = pyc.BaseTracer.exec
        orig_tracer_eval = pyc.BaseTracer.eval

        def _patched_exec(*args, **kwargs):
            with SafetyTracer.instance().tracing_disabled():
                return orig_exec(
                    *args,
                    num_extra_lookback_frames=kwargs.pop("num_extra_lookback_frames", 0)
                    + 1,
                    **kwargs,
                )

        def _patched_eval(*args, **kwargs):
            with SafetyTracer.instance().tracing_disabled():
                return orig_eval(
                    *args,
                    num_extra_lookback_frames=kwargs.pop("num_extra_lookback_frames", 0)
                    + 1,
                    **kwargs,
                )

        def _patched_tracer_exec(*args, **kwargs):
            with SafetyTracer.instance().tracing_disabled():
                return orig_tracer_exec(*args, **kwargs)

        def _patched_tracer_eval(*args, **kwargs):
            with SafetyTracer.instance().tracing_disabled():
                return orig_tracer_eval(*args, **kwargs)

        try:
            pyc.exec = _patched_exec
            pyc.eval = _patched_eval
            pyc.BaseTracer.exec = orig_tracer_exec
            pyc.BaseTracer.eval = orig_tracer_eval
            yield
        finally:
            pyc.exec = orig_exec
            pyc.eval = orig_eval
            pyc.BaseTracer.exec = orig_tracer_exec
            pyc.BaseTracer.eval = orig_tracer_eval

    async def safe_execute(self, cell_content: str, is_async: bool, run_cell_func):
        if self._saved_debug_message is not None:  # pragma: no cover
            logger.error(self._saved_debug_message)
            self._saved_debug_message = None
        ret = None
        with save_number_of_currently_executing_cell():
            cell_id, self._active_cell_id = self._active_cell_id, None
            assert cell_id is not None
            cell = cells().create_and_track(
                cell_id,
                cell_content,
                self._tags,
                validate_ipython_counter=self.settings.store_history,
            )

            # Stage 1: Precheck.
            self._safety_precheck_cell(cell)

            # Stage 2: Trace / run the cell, updating dependencies as they are encountered.
            try:
                with self._tracing_context():
                    if is_async:
                        ret = await run_cell_func(cell_content)  # pragma: no cover
                    else:
                        ret = run_cell_func(cell_content)
                # Stage 2.1: resync any defined symbols that could have gotten out-of-sync
                #  due to tracing being disabled

                self._resync_symbols(
                    [
                        # TODO: avoid bad performance by only iterating over symbols updated in this cell
                        sym
                        for sym in self.all_data_symbols()
                        if sym.timestamp.cell_num == cells().exec_counter()
                    ]
                )
                self._gc()
            except Exception as e:
                if self.is_test:
                    self.set_exception_raised_during_execution(e)
            finally:
                return ret

    def _make_cell_magic(self, cell_magic_name):
        # this is to avoid capturing `self` and creating an extra reference to the singleton
        store_history = self.settings.store_history

        def _run_cell_func(cell):
            run_cell(cell, store_history=store_history)

        def _dependency_safety(_, cell: str):
            asyncio.get_event_loop().run_until_complete(
                singletons.nbs().safe_execute(cell, False, _run_cell_func)
            )

        # FIXME (smacke): probably not a great idea to rely on this
        _dependency_safety.__name__ = cell_magic_name
        return register_cell_magic(_dependency_safety)

    @contextmanager
    def _patch_tracer_filters(
        self,
        tracer: pyc.BaseTracer,
    ) -> Generator[None, None, None]:
        orig_passes_filter = tracer.__class__.file_passes_filter_for_event
        orig_checker = tracer.__class__.should_instrument_file
        try:
            if not isinstance(tracer, (ModuleIniter, StackFrameManager)) or isinstance(
                tracer, SafetyTracer
            ):
                tracer.__class__.file_passes_filter_for_event = (
                    lambda *args: tracer.__class__ in self.registered_tracers
                    and orig_passes_filter(*args)
                )
            tracer.__class__.should_instrument_file = lambda *_: False
            yield
        finally:
            tracer.__class__.file_passes_filter_for_event = orig_passes_filter
            tracer.__class__.should_instrument_file = orig_checker

    def cleanup_tracers(self):
        for cleanup in reversed(self.tracer_cleanup_callbacks):
            cleanup()
        self.tracer_cleanup_callbacks.clear()
        self.tracer_cleanup_pending = False

    @contextmanager
    def _tracing_context(self):
        self.updated_symbols.clear()
        self.updated_reactive_symbols.clear()
        self.updated_deep_reactive_symbols.clear()

        try:
            all_tracers = [tracer.instance() for tracer in self.registered_tracers]
            if any(tracer.has_sys_trace_events for tracer in all_tracers):
                if not any(
                    isinstance(tracer, StackFrameManager) for tracer in all_tracers
                ):
                    # TODO: decouple this from the dataflow tracer
                    StackFrameManager.clear_instance()
                    all_tracers.append(StackFrameManager.instance())
            all_tracers.insert(0, ModuleIniter.instance())
            for tracer in all_tracers:
                tracer.reset()
            with pyc.multi_context(
                [self._patch_tracer_filters(tracer) for tracer in all_tracers]
            ):
                if len(self.tracer_cleanup_callbacks) == 0:
                    for tracer in all_tracers:
                        self.tracer_cleanup_callbacks.append(
                            tracer.tracing_non_context()
                        )
                else:
                    for tracer in all_tracers:
                        tracer._enable_tracing(check_disabled=False)
                ast_rewriter = SafetyTracer.instance().make_ast_rewriter(
                    module_id=self.cell_counter()
                )
                all_syntax_augmenters = []
                for tracer in all_tracers:
                    if (
                        isinstance(tracer, SafetyTracer)
                        and not self.settings.enable_reactive_modifiers
                    ):
                        continue
                    all_syntax_augmenters.extend(
                        tracer.make_syntax_augmenters(ast_rewriter)
                    )
                with input_transformer_context(all_syntax_augmenters):
                    with ast_transformer_context([ast_rewriter]):
                        with self._patch_pyccolo_exec_eval():
                            yield
                if self.tracer_cleanup_pending:
                    self.cleanup_tracers()
                else:
                    for tracer in all_tracers:
                        tracer._disable_tracing(check_enabled=False)
        except Exception:
            logger.exception("encountered an exception")
            raise

    def _make_line_magic(self):
        print_ = print  # to keep the test from failing since this is a legitimate print
        line_magic_names = [
            f[0] for f in inspect.getmembers(line_magics) if inspect.isfunction(f[1])
        ]

        def _handle(cmd, line):
            if cmd in ("deps", "show_deps", "show_dependency", "show_dependencies"):
                return line_magics.show_deps(line)
            elif cmd in ("stale", "show_stale"):
                return line_magics.show_stale(line)
            elif cmd == "trace_messages":
                return line_magics.trace_messages(line)
            elif cmd in ("hls", "nohls", "highlight", "highlights"):
                return line_magics.set_highlights(cmd, line)
            elif cmd in ("dag", "make_dag", "cell_dag", "make_cell_dag"):
                return json.dumps(self.create_dag_metadata(), indent=2)
            elif cmd in ("slice", "make_slice", "gather_slice"):
                return line_magics.make_slice(line)
            elif cmd in ("mode", "exec_mode"):
                return line_magics.set_exec_mode(line)
            elif cmd in ("schedule", "exec_schedule", "execution_schedule"):
                return line_magics.set_exec_schedule(line)
            elif cmd in ("flow", "flow_order", "semantics", "flow_semantics"):
                return line_magics.set_flow_order(line)
            elif cmd in ("register", "register_tracer"):
                return line_magics.register_tracer(line)
            elif cmd in ("deregister", "deregister_tracer"):
                return line_magics.deregister_tracer(line)
            elif cmd == "clear":
                self.min_timestamp = self.cell_counter()
                return None
            elif cmd in line_magic_names:
                logger.warning(
                    "We have a magic for %s, but have not yet registered it", cmd
                )
                return None
            else:
                logger.warning(line_magics.USAGE)
                return None

        def _safety(line: str):
            # this is to avoid capturing `self` and creating an extra reference to the singleton
            try:
                cmd, line = line.split(" ", 1)
                if cmd in ("slice", "make_slice", "gather_slice"):
                    # FIXME: hack to workaround some input transformer
                    line = re.sub(r"--tag +<class '(\w+)'>", r"--tag $\1", line)
            except ValueError:
                cmd, line = line, ""
            try:
                line, fname = line.split(">", 1)
            except ValueError:
                line, fname = line, None
            line = line.strip()
            if fname is not None:
                fname = fname.strip()

            outstr = _handle(cmd, line)
            if outstr is None:
                return

            if fname is None:
                print_(outstr)
            else:
                with open(fname, "w") as f:
                    f.write(outstr)

        # FIXME (smacke): probably not a great idea to rely on this
        _safety.__name__ = _SAFETY_LINE_MAGIC
        return register_line_magic(_safety)

    @property
    def dependency_tracking_enabled(self):
        return self.settings.track_dependencies

    @property
    def cell_magic_name(self):
        return self._cell_magic.__name__

    @property
    def line_magic_name(self):
        return self._line_magic.__name__

    def all_data_symbols(self) -> Iterable[DataSymbol]:
        for alias_set in self.aliases.values():
            yield from alias_set

    def test_and_clear_detected_flag(self):
        ret = self.safety_issue_detected
        self.safety_issue_detected = False
        return ret

    def _gc(self):
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
