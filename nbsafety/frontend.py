# -*- coding: utf-8 -*-
import logging
from collections import defaultdict
from typing import Any, Dict, List, Iterable, NamedTuple, Optional, Set, Tuple

from nbsafety.data_model.code_cell import cells, CheckerResult, ExecutedCodeCell
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.run_mode import ExecutionMode, ExecutionSchedule, FlowOrder
from nbsafety.singletons import nbs
from nbsafety.types import CellId


logger = logging.getLogger(__name__)


class FrontendCheckerResult(NamedTuple):
    stale_cells: Set[CellId]
    fresh_cells: Set[CellId]
    new_fresh_cells: Set[CellId]
    forced_reactive_cells: Set[CellId]
    typecheck_error_cells: Set[CellId]
    unsafe_order_cells: Dict[CellId, Set[ExecutedCodeCell]]
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
            unsafe_order_cells=defaultdict(set),
            stale_links=defaultdict(set),
            refresher_links=defaultdict(set),
            phantom_cell_info={},
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            # TODO: we should probably have separate fields for stale vs non-typechecking cells,
            #  or at least change the name to a more general "unsafe_cells" or equivalent
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

    def _compute_stale_and_refresher_links(self) -> None:
        stale_link_changes = True
        # transitive closure up until we hit non-stale refresher cells
        while stale_link_changes:
            stale_link_changes = False
            for stale_cell_id in self.stale_cells:
                new_stale_links = set(self.stale_links[stale_cell_id])
                original_length = len(new_stale_links)
                for refresher_cell_id in self.stale_links[stale_cell_id]:
                    if refresher_cell_id not in self.stale_cells:
                        continue
                    new_stale_links |= self.stale_links[refresher_cell_id]
                new_stale_links.discard(stale_cell_id)
                stale_link_changes = stale_link_changes or original_length != len(
                    new_stale_links
                )
                self.stale_links[stale_cell_id] = new_stale_links
        for stale_cell_id in self.stale_cells:
            self.stale_links[stale_cell_id] -= self.stale_cells
            for refresher_cell_id in self.stale_links[stale_cell_id]:
                self.refresher_links[refresher_cell_id].add(stale_cell_id)

    def _compute_refresher_cells(
        self,
        stale_symbols_by_cell_id: Dict[CellId, Set[DataSymbol]],
        killing_cell_ids_for_symbol: Dict[DataSymbol, Set[CellId]],
        last_executed_cell_id: Optional[CellId],
    ) -> None:
        nbs_ = nbs()
        eligible_refresher_for_dag = self.fresh_cells | self.stale_cells
        for stale_cell_id in self.stale_cells:
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
                    *(
                        killing_cell_ids_for_symbol[stale_sym]
                        for stale_sym in stale_syms
                    )
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
            self.stale_links[stale_cell_id] = refresher_cell_ids

    def _compute_reactive_cells_for_reactive_symbols(
        self, checker_results_by_cid: Dict[CellId, CheckerResult]
    ) -> None:
        nbs_ = nbs()
        if nbs_.mut_settings.exec_mode == ExecutionMode.REACTIVE:
            # no need to do this computation if already in reactive mode, since
            # everything that is new fresh is automatically considered reactive
            return
        for cell_id in self.new_fresh_cells:
            if cell_id not in checker_results_by_cid:
                continue
            cell = cells().from_id(cell_id)
            if cell.get_max_used_live_symbol_cell_counter(
                checker_results_by_cid[cell_id].live, filter_to_reactive=True
            ) > max(cell.cell_ctr, nbs_.min_timestamp):
                self.forced_reactive_cells.add(cell_id)

    def _compute_dag_based_staleness(
        self, cells_to_check: List[ExecutedCodeCell]
    ) -> None:
        nbs_ = nbs()
        if nbs_.mut_settings.exec_schedule != ExecutionSchedule.DAG_BASED:
            return
        prev_stale_cells: Set[CellId] = set()
        while True:
            for cell in cells_to_check:
                if cell.cell_id in self.stale_cells:
                    continue
                if nbs_.mut_settings.dynamic_slicing_enabled:
                    if cell.dynamic_parent_ids & (self.fresh_cells | self.stale_cells):
                        self.stale_cells.add(cell.cell_id)
                        continue
                if nbs_.mut_settings.static_slicing_enabled:
                    if cell.static_parent_ids & (self.fresh_cells | self.stale_cells):
                        self.stale_cells.add(cell.cell_id)
            if prev_stale_cells == self.stale_cells:
                break
            prev_stale_cells = set(self.stale_cells)
        self.fresh_cells.difference_update(self.stale_cells)
        self.new_fresh_cells.difference_update(self.stale_cells)
        for cell_id in self.stale_cells:
            cells().from_id(cell_id).set_fresh(False)

    def _compute_is_fresh(
        self, cell: ExecutedCodeCell, checker_result: CheckerResult
    ) -> bool:
        nbs_ = nbs()
        cell_id = cell.cell_id
        is_fresh = cell_id not in self.stale_cells
        if nbs_.mut_settings.exec_schedule == ExecutionSchedule.DAG_BASED:
            is_fresh = False
            flow_order = nbs_.mut_settings.flow_order
            if nbs_.mut_settings.dynamic_slicing_enabled:
                for par in cell.dynamic_parents:
                    if (
                        flow_order == flow_order.IN_ORDER
                        and par.position >= cell.position
                    ):
                        continue
                    if par.cell_ctr > max(cell.cell_ctr, nbs_.min_timestamp):
                        is_fresh = True
                        break
            if not is_fresh and nbs_.mut_settings.static_slicing_enabled:
                for par in cell.static_parents:
                    if (
                        flow_order == flow_order.IN_ORDER
                        and par.position >= cell.position
                    ):
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
        return is_fresh

    def _check_one_cell(
        self,
        cell: ExecutedCodeCell,
        update_liveness_time_versions: bool,
        last_executed_cell_pos: int,
        stale_symbols_by_cell_id: Dict[CellId, Set[DataSymbol]],
        killing_cell_ids_for_symbol: Dict[DataSymbol, Set[CellId]],
        phantom_cell_info: Dict[CellId, Dict[CellId, Set[int]]],
    ) -> Optional[CheckerResult]:
        nbs_ = nbs()
        try:
            checker_result = cell.check_and_resolve_symbols(
                update_liveness_time_versions=update_liveness_time_versions
            )
        except SyntaxError:
            return None
        cell_id = cell.cell_id
        if (
            nbs_.mut_settings.flow_order == FlowOrder.IN_ORDER
            or nbs_.mut_settings.exec_schedule == ExecutionSchedule.STRICT
        ):
            for live_sym in checker_result.live:
                if not live_sym.is_deep or not live_sym.timestamp.is_initialized:
                    continue
                updated_cell = cells().from_timestamp(live_sym.timestamp)
                if updated_cell.position > cell.position:
                    self.unsafe_order_cells[cell_id].add(updated_cell)
        if nbs_.mut_settings.flow_order == FlowOrder.IN_ORDER:
            if (
                last_executed_cell_pos is not None
                and cell.position <= last_executed_cell_pos
            ):
                return checker_result
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
            self.stale_cells.add(cell_id)
        if not checker_result.typechecks:
            self.typecheck_error_cells.add(cell_id)
        for dead_sym in checker_result.dead:
            killing_cell_ids_for_symbol[dead_sym].add(cell_id)

        if nbs_.settings.mark_phantom_cell_usages_unsafe:
            phantom_cell_info_for_cell = cell.compute_phantom_cell_info(
                checker_result.used_cells
            )
            if len(phantom_cell_info_for_cell) > 0:
                phantom_cell_info[cell_id] = phantom_cell_info_for_cell
        is_fresh = self._compute_is_fresh(cell, checker_result)
        if is_fresh:
            self.fresh_cells.add(cell_id)
        if not cells().from_id(cell_id).set_fresh(is_fresh) and is_fresh:
            self.new_fresh_cells.add(cell_id)
        return checker_result

    def _get_last_executed_pos_and_handle_reactive_tags(
        self,
        last_executed_cell_id: Optional[CellId],
    ) -> Optional[int]:
        if last_executed_cell_id is None:
            return None
        last_executed_cell = cells().from_id(last_executed_cell_id)
        for tag in last_executed_cell.tags:
            for reactive_cell_id in cells().get_reactive_ids_for_tag(tag):
                self.forced_reactive_cells.add(reactive_cell_id)
        return last_executed_cell.position

    def compute_frontend_checker_result(
        self,
        cells_to_check: Optional[Iterable[ExecutedCodeCell]] = None,
        update_liveness_time_versions: bool = False,
        last_executed_cell_id: Optional[CellId] = None,
    ) -> "FrontendCheckerResult":
        nbs_ = nbs()
        stale_symbols_by_cell_id: Dict[CellId, Set[DataSymbol]] = {}
        killing_cell_ids_for_symbol: Dict[DataSymbol, Set[CellId]] = defaultdict(set)
        phantom_cell_info: Dict[CellId, Dict[CellId, Set[int]]] = {}
        checker_results_by_cid: Dict[CellId, CheckerResult] = {}
        last_executed_cell_pos = self._get_last_executed_pos_and_handle_reactive_tags(
            last_executed_cell_id
        )
        if cells_to_check is None:
            cells_to_check = cells().all_cells_most_recently_run_for_each_id()
        cells_to_check = sorted(cells_to_check, key=lambda c: c.position)
        for cell in cells_to_check:
            checker_result = self._check_one_cell(
                cell,
                update_liveness_time_versions,
                last_executed_cell_pos,
                stale_symbols_by_cell_id,
                killing_cell_ids_for_symbol,
                phantom_cell_info,
            )
            if checker_result is not None:
                checker_results_by_cid[cell.cell_id] = checker_result
            if (
                nbs_.mut_settings.exec_schedule == ExecutionSchedule.STRICT
                and cell.is_fresh
            ):
                # in the case of strict scheduling, don't bother checking
                # anything else once we get to the first fresh cell
                break

        self._compute_dag_based_staleness(cells_to_check)
        self._compute_reactive_cells_for_reactive_symbols(checker_results_by_cid)
        self._compute_refresher_cells(
            stale_symbols_by_cell_id,
            killing_cell_ids_for_symbol,
            last_executed_cell_id,
        )
        self._compute_stale_and_refresher_links()
        return self
