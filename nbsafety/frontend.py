# -*- coding: utf-8 -*-
from collections import defaultdict
from typing import Any, Dict, Iterable, NamedTuple, Optional, Set

from nbsafety.data_model.code_cell import cells, CheckerResult, ExecutedCodeCell
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.run_mode import ExecutionMode, ExecutionSchedule, FlowOrder
from nbsafety.singletons import kernel, nbs
from nbsafety.tracing.nbsafety_tracer import SafetyTracer
from nbsafety.types import CellId


class FrontendCheckerResult(NamedTuple):
    stale_cells: Set[CellId]
    fresh_cells: Set[CellId]
    new_fresh_cells: Set[CellId]
    forced_reactive_cells: Set[CellId]
    typecheck_error_cells: Set[CellId]
    # unsafe_order_cells: Set[CellId]
    stale_links: Dict[CellId, Set[CellId]]
    refresher_links: Dict[CellId, Set[CellId]]
    phantom_cell_info: Dict[CellId, Dict[CellId, Set[int]]]

    @classmethod
    def empty(cls):
        return cls(
            stale_cells=set(),
            fresh_cells=set(),
            new_fresh_cells=set(),
            forced_reactive_cells=set(),
            typecheck_error_cells=set(),
            # unsafe_order_cells=set(),
            stale_links=defaultdict(set),
            refresher_links=defaultdict(set),
            phantom_cell_info={},
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            # TODO: we should probably have separate fields for stale vs non-typechecking cells,
            #  or at least change the name to a more general "unsafe_cells" or equivalent
            # "stale_cells": list(self.stale_cells | self.typecheck_error_cells | self.unsafe_order_cells),
            "stale_cells": list(self.stale_cells | self.typecheck_error_cells),
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


break_ = object()


def _check_one_cell(
    cell: ExecutedCodeCell,
    result: FrontendCheckerResult,
    update_liveness_time_versions: bool,
    last_executed_cell_pos: int,
    checker_results_by_cid: Dict[CellId, CheckerResult],
    stale_symbols_by_cell_id: Dict[CellId, Set[DataSymbol]],
    killing_cell_ids_for_symbol: Dict[DataSymbol, Set[CellId]],
    phantom_cell_info: Dict[CellId, Dict[CellId, Set[int]]],
):
    nbs_ = nbs()
    try:
        checker_result = cell.check_and_resolve_symbols(
            update_liveness_time_versions=update_liveness_time_versions
        )
    except SyntaxError:
        return
    cell_id = cell.cell_id
    checker_results_by_cid[cell_id] = checker_result
    # if self.mut_settings.flow_order == FlowOrder.IN_ORDER:
    #     for live_sym in checker_result.live:
    #         if cells().from_timestamp(live_sym.timestamp).position > cell.position:
    #             unsafe_order_cells.add(cell_id)
    #             break
    if nbs_.mut_settings.flow_order == FlowOrder.IN_ORDER:
        if (
            last_executed_cell_pos is not None
            and cell.position <= last_executed_cell_pos
        ):
            return
    if nbs_.mut_settings.exec_schedule == ExecutionSchedule.LIVENESS_BASED:
        stale_symbols = {
            sym.dsym
            for sym in checker_result.live
            if sym.is_stale_at_position(cell.position)
        }
    else:
        stale_symbols = set()
    if len(stale_symbols) > 0:
        stale_symbols_by_cell_id[cell_id] = stale_symbols
        result.stale_cells.add(cell_id)
    if not checker_result.typechecks:
        result.typecheck_error_cells.add(cell_id)
    for dead_sym in checker_result.dead:
        killing_cell_ids_for_symbol[dead_sym].add(cell_id)

    is_fresh = cell_id not in result.stale_cells
    if nbs_.settings.mark_phantom_cell_usages_unsafe:
        phantom_cell_info_for_cell = cell.compute_phantom_cell_info(
            checker_result.used_cells
        )
        if len(phantom_cell_info_for_cell) > 0:
            phantom_cell_info[cell_id] = phantom_cell_info_for_cell
    if nbs_.mut_settings.exec_schedule == ExecutionSchedule.DAG_BASED:
        is_fresh = False
        flow_order = nbs_.mut_settings.flow_order
        if nbs_.mut_settings.dynamic_slicing_enabled:
            for par in cell.dynamic_parents:
                if flow_order == flow_order.IN_ORDER and par.position >= cell.position:
                    continue
                if par.cell_ctr > max(cell.cell_ctr, nbs_.min_timestamp):
                    is_fresh = True
                    break
        if not is_fresh and nbs_.mut_settings.static_slicing_enabled:
            for par in cell.static_parents:
                if flow_order == flow_order.IN_ORDER and par.position >= cell.position:
                    continue
                if par.cell_ctr > max(cell.cell_ctr, nbs_.min_timestamp):
                    is_fresh = True
                    break
    else:
        is_fresh = is_fresh and (
            cell.get_max_used_live_symbol_cell_counter(checker_result.live)
            > max(cell.cell_ctr, nbs_.min_timestamp)
        )
    if nbs_.mut_settings.exec_schedule == ExecutionSchedule.STRICT:
        for dead_sym in checker_result.dead:
            if dead_sym.timestamp.cell_num > max(cell.cell_ctr, nbs_.min_timestamp):
                is_fresh = True
    if is_fresh:
        result.fresh_cells.add(cell_id)
    if not cells().from_id(cell_id).set_fresh(is_fresh) and is_fresh:
        result.new_fresh_cells.add(cell_id)
    if is_fresh and nbs_.mut_settings.exec_schedule == ExecutionSchedule.STRICT:
        return break_


def compute_frontend_cell_metadata(
    cells_to_check: Optional[Iterable[ExecutedCodeCell]] = None,
    update_liveness_time_versions: bool = False,
    last_executed_cell_id: Optional[CellId] = None,
) -> FrontendCheckerResult:
    result = FrontendCheckerResult.empty()
    if SafetyTracer not in kernel().registered_tracers:
        return result
    nbs_ = nbs()
    for tracer in kernel().registered_tracers:
        # force initialization here in case not already inited
        tracer.instance()
    stale_symbols_by_cell_id: Dict[CellId, Set[DataSymbol]] = {}
    killing_cell_ids_for_symbol: Dict[DataSymbol, Set[CellId]] = defaultdict(set)
    phantom_cell_info: Dict[CellId, Dict[CellId, Set[int]]] = {}
    checker_results_by_cid: Dict[CellId, CheckerResult] = {}
    if last_executed_cell_id is None:
        last_executed_cell = None
        last_executed_cell_pos = None
    else:
        last_executed_cell = cells().from_id(last_executed_cell_id)
        last_executed_cell_pos = last_executed_cell.position
        for tag in last_executed_cell.tags:
            for reactive_cell_id in cells().get_reactive_ids_for_tag(tag):
                result.forced_reactive_cells.add(reactive_cell_id)
    if cells_to_check is None:
        cells_to_check = cells().all_cells_most_recently_run_for_each_id()
    cells_to_check = sorted(cells_to_check, key=lambda c: c.position)
    for cell in cells_to_check:
        if (
            _check_one_cell(
                cell,
                result,
                update_liveness_time_versions,
                last_executed_cell_pos,
                checker_results_by_cid,
                stale_symbols_by_cell_id,
                killing_cell_ids_for_symbol,
                phantom_cell_info,
            )
            is break_
        ):
            break

    if nbs_.mut_settings.exec_schedule == ExecutionSchedule.DAG_BASED:
        prev_stale_cells: Set[CellId] = set()
        while True:
            for cell in cells_to_check:
                if cell.cell_id in result.stale_cells:
                    continue
                if nbs_.mut_settings.dynamic_slicing_enabled:
                    if cell.dynamic_parent_ids & (
                        result.fresh_cells | result.stale_cells
                    ):
                        result.stale_cells.add(cell.cell_id)
                        continue
                if nbs_.mut_settings.static_slicing_enabled:
                    if cell.static_parent_ids & (
                        result.fresh_cells | result.stale_cells
                    ):
                        result.stale_cells.add(cell.cell_id)
            if prev_stale_cells == result.stale_cells:
                break
            prev_stale_cells = set(result.stale_cells)
        result.fresh_cells -= result.stale_cells
        result.new_fresh_cells -= result.stale_cells
        for cell_id in result.stale_cells:
            cells().from_id(cell_id).set_fresh(False)
    if nbs_.mut_settings.exec_mode != ExecutionMode.REACTIVE:
        for cell_id in result.new_fresh_cells:
            if cell_id not in checker_results_by_cid:
                continue
            cell = cells().from_id(cell_id)
            if cell.get_max_used_live_symbol_cell_counter(
                checker_results_by_cid[cell_id].live, filter_to_reactive=True
            ) > max(cell.cell_ctr, nbs_.min_timestamp):
                result.forced_reactive_cells.add(cell_id)
    eligible_refresher_for_dag = result.fresh_cells | result.stale_cells
    for stale_cell_id in result.stale_cells:
        refresher_cell_ids: Set[CellId] = set()
        if nbs_.mut_settings.flow_order == ExecutionSchedule.DAG_BASED:
            if nbs_.mut_settings.dynamic_slicing_enabled:
                refresher_cell_ids |= (
                    cells().from_id(stale_cell_id).dynamic_parent_ids
                    & eligible_refresher_for_dag
                )
            if nbs_.mut_settings.static_slicing_enabled:
                refresher_cell_ids |= (
                    cells().from_id(stale_cell_id).static_parent_ids
                    & eligible_refresher_for_dag
                )
        else:
            stale_syms = stale_symbols_by_cell_id.get(stale_cell_id, set())
            refresher_cell_ids = refresher_cell_ids.union(
                *(killing_cell_ids_for_symbol[stale_sym] for stale_sym in stale_syms)
            )
        if nbs_.mut_settings.flow_order == FlowOrder.IN_ORDER:
            refresher_cell_ids = {
                cid
                for cid in refresher_cell_ids
                if cells().from_id(cid).position
                < cells().from_id(stale_cell_id).position
            }
        if last_executed_cell_id is not None:
            refresher_cell_ids.discard(last_executed_cell_id)
        result.stale_links[stale_cell_id] = refresher_cell_ids
    stale_link_changes = True
    # transitive closure up until we hit non-stale refresher cells
    while stale_link_changes:
        stale_link_changes = False
        for stale_cell_id in result.stale_cells:
            new_stale_links = set(result.stale_links[stale_cell_id])
            original_length = len(new_stale_links)
            for refresher_cell_id in result.stale_links[stale_cell_id]:
                if refresher_cell_id not in result.stale_cells:
                    continue
                new_stale_links |= result.stale_links[refresher_cell_id]
            new_stale_links.discard(stale_cell_id)
            stale_link_changes = stale_link_changes or original_length != len(
                new_stale_links
            )
            result.stale_links[stale_cell_id] = new_stale_links
    for stale_cell_id in result.stale_cells:
        result.stale_links[stale_cell_id] -= result.stale_cells
        for refresher_cell_id in result.stale_links[stale_cell_id]:
            result.refresher_links[refresher_cell_id].add(stale_cell_id)
    return result
