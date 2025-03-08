# -*- coding: utf-8 -*-
import ast
import logging
from collections import defaultdict
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

from ipyflow.config import ExecutionSchedule, FlowDirection
from ipyflow.data_model.cell import Cell, CheckerResult, cells
from ipyflow.data_model.symbol import Symbol
from ipyflow.singletons import flow
from ipyflow.slicing.context import SlicingContext, slicing_ctx_var
from ipyflow.types import IdType

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


def _make_range_from_node(node: ast.AST) -> Dict[str, Any]:
    return {
        "start": {
            "line": node.lineno - 1,  # type: ignore[attr-defined]
            "character": node.col_offset,  # type: ignore[attr-defined]
        },
        "end": {
            "line": getattr(node, "end_lineno", 0) - 1,
            "character": getattr(node, "end_col_offset", 0) - 1,
        },
    }


class FrontendCheckerResult(NamedTuple):
    cell_parents: Dict[IdType, Set[IdType]]
    cell_children: Dict[IdType, Set[IdType]]
    waiting_cells: Set[IdType]
    ready_cells: Set[IdType]
    new_ready_cells: Set[IdType]
    forced_reactive_cells: Set[IdType]
    forced_cascading_reactive_cells: Set[IdType]
    typecheck_error_cells: Set[IdType]
    unsafe_order_cells: Dict[IdType, Set[Cell]]
    unsafe_order_symbol_usage: Dict[IdType, List[Dict[str, Any]]]
    waiter_links: Dict[IdType, Set[IdType]]
    ready_maker_links: Dict[IdType, Set[IdType]]
    stale_parents: Dict[IdType, Set[IdType]]
    stale_parents_by_executed_cell_by_child: Dict[IdType, Dict[IdType, Set[IdType]]]
    stale_parents_by_child_by_executed_cell: Dict[IdType, Dict[IdType, Set[IdType]]]
    phantom_cell_info: Dict[IdType, Dict[IdType, Set[int]]]
    allow_new_ready: bool

    @classmethod
    def empty(cls, allow_new_ready: bool = True):
        return cls(
            cell_parents={},
            cell_children={},
            waiting_cells=set(),
            ready_cells=set(),
            new_ready_cells=set(),
            forced_reactive_cells=set(),
            forced_cascading_reactive_cells=set(),
            typecheck_error_cells=set(),
            unsafe_order_cells=defaultdict(set),
            unsafe_order_symbol_usage=defaultdict(list),
            waiter_links=defaultdict(set),
            ready_maker_links=defaultdict(set),
            stale_parents=defaultdict(set),
            stale_parents_by_executed_cell_by_child={},
            stale_parents_by_child_by_executed_cell={},
            phantom_cell_info={},
            allow_new_ready=allow_new_ready,
        )

    def to_json(self) -> Dict[str, Any]:
        return {
            # TODO: we should probably have separate fields for waiting vs non-typechecking cells,
            #  or at least change the name to a more general "unsafe_cells" or equivalent
            "cell_parents": {
                cell_id: list(parent_ids)
                for cell_id, parent_ids in self.cell_parents.items()
            },
            "cell_children": {
                cell_id: list(child_ids)
                for cell_id, child_ids in self.cell_children.items()
            },
            "waiting_cells": list(self.waiting_cells | self.typecheck_error_cells),
            "ready_cells": list(self.ready_cells),
            "new_ready_cells": (
                list(self.new_ready_cells) if self.allow_new_ready else []
            ),
            "forced_reactive_cells": list(self.forced_reactive_cells),
            "forced_cascading_reactive_cells": list(
                self.forced_cascading_reactive_cells
            ),
            "unsafe_order_cells": {
                cell_id: [unsafe.cell_id for unsafe in unsafe_order_cells]
                for cell_id, unsafe_order_cells in self.unsafe_order_cells.items()
            },
            "unsafe_order_symbol_usage": self.unsafe_order_symbol_usage,
            "waiter_links": {
                cell_id: list(linked_cell_ids)
                for cell_id, linked_cell_ids in self.waiter_links.items()
            },
            "ready_maker_links": {
                cell_id: list(linked_cell_ids)
                for cell_id, linked_cell_ids in self.ready_maker_links.items()
            },
            "stale_parents": {
                cell_id: list(linked_cell_ids)
                for cell_id, linked_cell_ids in self.stale_parents.items()
            },
            "stale_parents_by_executed_cell_by_child": {
                cell_id: {
                    executed_cell_id: list(stale_parent_ids)
                    for executed_cell_id, stale_parent_ids in stale_parents.items()
                }
                for cell_id, stale_parents in self.stale_parents_by_executed_cell_by_child.items()
            },
            "stale_parents_by_child_by_executed_cell": {
                executed_cell_id: {
                    cell_id: list(stale_parent_ids)
                    for cell_id, stale_parent_ids in stale_parents.items()
                }
                for executed_cell_id, stale_parents in self.stale_parents_by_child_by_executed_cell.items()
            },
        }

    def _compute_waiter_and_ready_maker_links(self) -> None:
        waiter_link_changes = True
        # transitive closure up until we hit non-waiting ready-making cells
        while waiter_link_changes:
            waiter_link_changes = False
            for waiting_cell_id in self.waiting_cells:
                new_waiter_links = set(self.waiter_links[waiting_cell_id])
                original_length = len(new_waiter_links)
                for ready_making_cell_id in self.waiter_links[waiting_cell_id]:
                    if ready_making_cell_id not in self.waiting_cells:
                        continue
                    new_waiter_links |= self.waiter_links[ready_making_cell_id]
                new_waiter_links.discard(waiting_cell_id)
                waiter_link_changes = waiter_link_changes or original_length != len(
                    new_waiter_links
                )
                self.waiter_links[waiting_cell_id] = new_waiter_links
        for waiting_cell_id in self.waiting_cells:
            self.waiter_links[waiting_cell_id] -= self.waiting_cells
            for ready_making_cell_id in self.waiter_links[waiting_cell_id]:
                self.ready_maker_links[ready_making_cell_id].add(waiting_cell_id)

    def _compute_ready_making_cells(
        self,
        waiting_symbols_by_cell_id: Dict[IdType, Set[Symbol]],
        killing_cell_ids_for_symbol: Dict[Symbol, Set[IdType]],
        last_executed_cell_id: Optional[IdType],
    ) -> None:
        flow_ = flow()
        eligible_ready_making_for_dag = self.ready_cells | self.waiting_cells
        for waiting_cell_id in self.waiting_cells:
            ready_making_cell_ids: Set[IdType] = set()
            if flow_.mut_settings.exec_schedule in (
                ExecutionSchedule.DAG_BASED,
                ExecutionSchedule.HYBRID_DAG_LIVENESS_BASED,
            ):
                for _ in flow_.mut_settings.iter_slicing_contexts():
                    ready_making_cell_ids |= (
                        cells().from_id(waiting_cell_id).directional_parents.keys()
                        & eligible_ready_making_for_dag
                    )
            else:
                waiting_syms = waiting_symbols_by_cell_id.get(waiting_cell_id, set())
                ready_making_cell_ids = ready_making_cell_ids.union(
                    *(
                        killing_cell_ids_for_symbol[waiting_sym]
                        for waiting_sym in waiting_syms
                    )
                )
            if flow_.mut_settings.flow_order == FlowDirection.IN_ORDER:
                ready_making_cell_ids = {
                    cid
                    for cid in ready_making_cell_ids
                    if cells().from_id(cid).position
                    < cells().from_id(waiting_cell_id).position
                }
            if last_executed_cell_id is not None:
                ready_making_cell_ids.discard(last_executed_cell_id)
            self.waiter_links[waiting_cell_id] = ready_making_cell_ids

    def _compute_reactive_cells_for_reactive_symbols(
        self,
        checker_results_by_cid: Dict[IdType, CheckerResult],
        last_executed_cell_pos: int,
    ) -> None:
        flow_ = flow()
        for cell_id in self.ready_cells:
            if cell_id not in checker_results_by_cid:
                continue
            cell = cells().from_id(cell_id)
            if (
                flow_.mut_settings.flow_order == FlowDirection.IN_ORDER
                and cell.position < last_executed_cell_pos
            ):
                # prevent this cell from being reactive if it appears before the last executed cell
                continue
            max_used_ctr = cell.get_max_used_live_symbol_cell_counter(
                checker_results_by_cid[cell_id].live, filter_to_reactive=True
            )
            if max_used_ctr > max(
                cell.cell_ctr, flow_.min_forced_reactive_cell_counter()
            ):
                self.forced_reactive_cells.add(cell_id)
            max_used_ctr = cell.get_max_used_live_symbol_cell_counter(
                checker_results_by_cid[cell_id].live, filter_to_cascading_reactive=True
            )
            if max_used_ctr > max(
                cell.cell_ctr, flow_.min_forced_reactive_cell_counter()
            ):
                self.forced_cascading_reactive_cells.add(cell_id)

    def _compute_dag_based_waiters(self, cells_to_check: List[Cell]) -> None:
        flow_ = flow()
        if flow_.mut_settings.exec_schedule not in (
            ExecutionSchedule.DAG_BASED,
            ExecutionSchedule.HYBRID_DAG_LIVENESS_BASED,
        ):
            return
        prev_waiting_cells: Set[IdType] = set()
        while True:
            for cell in cells_to_check:
                if cell.cell_id in self.waiting_cells:
                    continue
                for _ in flow_.mut_settings.iter_slicing_contexts():
                    if cell.directional_parents.keys() & (
                        self.ready_cells | self.waiting_cells
                    ):
                        self.waiting_cells.add(cell.cell_id)
                        continue
            if prev_waiting_cells == self.waiting_cells:
                break
            prev_waiting_cells = set(self.waiting_cells)
        self.ready_cells.difference_update(self.waiting_cells)
        self.new_ready_cells.difference_update(self.waiting_cells)
        for cell_id in self.waiting_cells:
            cells().from_id(cell_id).set_ready(False)

    def _compute_stale_parents(self, cell: Cell) -> None:
        flow_ = flow()
        if (
            flow_.mut_settings.exec_schedule
            not in (
                ExecutionSchedule.DAG_BASED,
                ExecutionSchedule.HYBRID_DAG_LIVENESS_BASED,
            )
            or flow_.mut_settings.flow_order != FlowDirection.IN_ORDER
        ):
            return
        for _ in flow_.mut_settings.iter_slicing_contexts():
            for pid, syms in cell.directional_parents.items():
                parent = cells().from_id(pid)
                for sym in syms:
                    if sym.shallow_timestamp.cell_num > parent.cell_ctr:
                        self.stale_parents[cell.cell_id].add(parent.cell_id)
                        break

    def _compute_stale_parent_makers(self) -> None:
        flow_ = flow()
        if (
            flow_.mut_settings.exec_schedule
            not in (
                ExecutionSchedule.DAG_BASED,
                ExecutionSchedule.HYBRID_DAG_LIVENESS_BASED,
            )
            or flow_.mut_settings.flow_order != FlowDirection.IN_ORDER
        ):
            return
        cells_so_far_that_update_symbol: Dict[Symbol, Set[Cell]] = {}
        for cell in cells().iterate_over_notebook_in_position_order():
            for _ in flow_.mut_settings.iter_slicing_contexts():
                for pid, syms in cell.raw_parents.items():
                    for sym in syms:
                        for executed_cell in cells_so_far_that_update_symbol.get(
                            sym, []
                        ):
                            self.stale_parents_by_executed_cell_by_child.setdefault(
                                cell.cell_id, {}
                            ).setdefault(executed_cell.cell_id, set()).add(pid)
                            self.stale_parents_by_child_by_executed_cell.setdefault(
                                executed_cell.cell_id, {}
                            ).setdefault(cell.cell_id, set()).add(pid)
            static_writes = set(cell.static_writes)
            if cell.last_check_result is not None:
                static_writes &= cell.last_check_result.modified
            for sym in static_writes | cell.dynamic_writes:
                cells_so_far_that_update_symbol.setdefault(sym, set()).add(cell)

    def _compute_readiness(
        self, cell: Cell, checker_result: CheckerResult
    ) -> Tuple[bool, bool]:
        flow_ = flow()
        cell_id = cell.cell_id
        if cell_id in self.waiting_cells:
            return False, False
        is_ready = False
        is_new_ready = False
        exec_schedule = flow_.mut_settings.exec_schedule
        flow_order = flow_.mut_settings.flow_order
        if flow_.mut_settings.exec_schedule in (
            ExecutionSchedule.DAG_BASED,
            ExecutionSchedule.HYBRID_DAG_LIVENESS_BASED,
        ):
            latest_par_by_ts = cell.get_latest_parent_by_ts_map()
            for _ in flow_.mut_settings.iter_slicing_contexts():
                if is_new_ready:
                    break
                if is_new_ready:
                    break
                for pid, raw_syms in cell.directional_parents.items():
                    par = cells().from_id(pid)
                    syms = raw_syms - cell.static_removed_symbols
                    if flow_.fake_edge_sym in syms and cell.cell_ctr < 0 < par.cell_ctr:
                        is_ready = True
                        break
                    if (
                        latest_par_by_ts is not None
                        and flow_.fake_edge_sym not in syms
                        and pid
                        not in {
                            latest_par_by_ts[sym.shallow_timestamp].cell_id
                            for sym in syms
                        }
                    ):
                        continue
                    if (
                        max(cell.cell_ctr, flow_.min_timestamp) < par.cell_ctr
                        and (
                            flow_.mut_settings.pull_reactive_updates
                            or par.cell_ctr
                            in {sym.shallow_timestamp.cell_num for sym in syms}
                        )
                    ) or par.cell_ctr in {
                        sym.visible_timestamp.cell_num
                        for sym in syms
                        if sym.visible_timestamp is not None
                        and sym.visible_timestamp.cell_num
                        != sym.shallow_timestamp.cell_num
                    }:
                        should_skip = False
                        if (
                            flow_order == FlowDirection.IN_ORDER
                            and slicing_ctx_var.get() == SlicingContext.STATIC
                        ):
                            for (
                                other_pid,
                                other_syms,
                            ) in cell.directional_parents.items():
                                other_parent = cells().from_id(other_pid)
                                if other_parent.position <= par.position:
                                    continue
                                if (
                                    syms <= other_syms
                                    or flow_.fake_edge_sym in other_syms
                                ):
                                    should_skip = True
                                    break
                        if should_skip:
                            continue
                        is_ready = True
                        if (
                            par.cell_ctr >= flow_.min_new_ready_cell_counter()
                            and cell.cell_ctr > 0
                        ):
                            is_new_ready = True
                            break
        if not is_new_ready and (
            exec_schedule == ExecutionSchedule.LIVENESS_BASED
            or (
                exec_schedule == ExecutionSchedule.HYBRID_DAG_LIVENESS_BASED
                and flow_order == FlowDirection.IN_ORDER
            )
        ):
            max_used_live_sym_ctr = cell.get_max_used_live_symbol_cell_counter(
                checker_result.live, dead_symbols=checker_result.dead
            )
            if max_used_live_sym_ctr > max(cell.cell_ctr, flow_.min_timestamp):
                is_ready = True
                if (
                    cell.cell_ctr > 0
                    and max_used_live_sym_ctr >= flow_.min_new_ready_cell_counter()
                ):
                    is_new_ready = True
        return is_ready, is_new_ready

    def _check_one_cell(
        self,
        cell: Cell,
        update_liveness_time_versions: bool,
        last_executed_cell_pos: Optional[int],
        waiting_symbols_by_cell_id: Dict[IdType, Set[Symbol]],
        killing_cell_ids_for_symbol: Dict[Symbol, Set[IdType]],
        phantom_cell_info: Dict[IdType, Dict[IdType, Set[int]]],
    ) -> Optional[CheckerResult]:
        flow_ = flow()
        try:
            checker_result = cell.check_and_resolve_symbols(
                update_liveness_time_versions=update_liveness_time_versions
            )
        except SyntaxError:
            return None
        except Exception:
            if flow_.is_dev_mode:
                logger.exception("exception occurred during checking")
            return None
        cell_id = cell.cell_id
        if flow_.mut_settings.flow_order == FlowDirection.IN_ORDER:
            for live_sym in checker_result.live:
                if not live_sym.is_deep or not live_sym.timestamp.is_initialized:
                    continue
                updated_cell = cells().at_timestamp(live_sym.timestamp)
                if updated_cell.position > cell.position:
                    self.unsafe_order_cells[cell_id].add(updated_cell)
        if flow_.mut_settings.exec_schedule == ExecutionSchedule.LIVENESS_BASED:
            waiting_symbols = {
                sym.sym
                for sym in checker_result.live
                if sym.is_waiting_at_position(cell.position)
            }
            unresolved_live_refs = checker_result.unresolved_live_refs
        else:
            waiting_symbols = set()
            unresolved_live_refs = set()
        if len(waiting_symbols) > 0:
            waiting_symbols_by_cell_id[cell_id] = waiting_symbols
        if len(waiting_symbols) > 0 or len(unresolved_live_refs) > 0:
            self.waiting_cells.add(cell_id)
        if not checker_result.typechecks:
            self.typecheck_error_cells.add(cell_id)
        for dead_sym in checker_result.dead:
            killing_cell_ids_for_symbol[dead_sym].add(cell_id)

        if flow_.settings.mark_phantom_cell_usages_unsafe:
            phantom_cell_info_for_cell = cell.compute_phantom_cell_info(
                checker_result.used_cells
            )
            if len(phantom_cell_info_for_cell) > 0:
                phantom_cell_info[cell_id] = phantom_cell_info_for_cell
        self._compute_stale_parents(cell)
        self._compute_stale_parent_makers()
        is_ready, is_new_ready = self._compute_readiness(cell, checker_result)
        if is_ready:
            self.ready_cells.add(cell_id)
        was_ready = cell.set_ready(is_ready)
        if flow_.mut_settings.flow_order == FlowDirection.IN_ORDER:
            if (
                last_executed_cell_pos is not None
                and cell.position <= last_executed_cell_pos
            ):
                # prevent this cell from being considered as newly ready so that
                # it is not reactively executed
                return checker_result
        if is_new_ready or (
            cell.cell_ctr > 0
            and not was_ready
            and is_ready
            and flow_.cell_counter() >= flow_.min_new_ready_cell_counter()
        ):
            self.new_ready_cells.add(cell_id)
        return checker_result

    def _get_last_executed_pos_and_handle_reactive_tags(
        self,
        last_executed_cell_id: Optional[IdType],
    ) -> Optional[int]:
        if last_executed_cell_id is None:
            return None
        last_executed_cell = cells().from_id(last_executed_cell_id)
        if last_executed_cell is None:
            return None
        for tag in last_executed_cell.tags:
            for reactive_cell_id in cells().get_reactive_ids_for_tag(tag):
                self.forced_reactive_cells.add(reactive_cell_id)
        return last_executed_cell.position

    def _compute_unsafe_order_usages(self, cells_to_check: List[Cell]) -> None:
        # FIXME: this will be slow for large notebooks; speed it up
        #  or make it optional
        cell_by_ctr: Dict[int, Cell] = {cell.cell_ctr: cell for cell in cells_to_check}
        for sym in flow().all_symbols():
            if sym.is_anonymous:
                continue
            for used_ts, ts_when_used in sym.timestamp_by_used_time.items():
                cell = cell_by_ctr.get(used_ts.cell_num, None)
                if cell is None:
                    continue
                if cells().at_timestamp(ts_when_used).position <= cell.position:
                    continue
                used_node = sym.used_node_by_used_time.get(used_ts, None)
                if used_node is None or not all(
                    hasattr(used_node, pos_attr)
                    for pos_attr in (
                        "lineno",
                        "end_lineno",
                        "col_offset",
                        "end_col_offset",
                    )
                ):
                    continue
                self.unsafe_order_symbol_usage[cell.cell_id].append(
                    {
                        "name": sym.readable_name,
                        "range": _make_range_from_node(used_node),
                        "last_updated_cell": ts_when_used.cell_num,
                    },
                )

    def _compute_filtered_parents(self, cells_to_check: List[Cell]) -> None:
        flow_ = flow()
        for cell in cells_to_check:
            this_cell_parents: Set[IdType] = set()
            latest_par_by_ts = cell.get_latest_parent_by_ts_map()
            for _ in flow_.mut_settings.iter_slicing_contexts():
                for par_id, raw_syms in cell.directional_parents.items():
                    syms = raw_syms - cell.static_removed_symbols
                    if len(syms) == 0:
                        continue
                    if (
                        latest_par_by_ts is not None
                        and flow_.fake_edge_sym not in syms
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
                        flow_.mut_settings.flow_order == FlowDirection.IN_ORDER
                        and slicing_ctx_var.get() == SlicingContext.STATIC
                    ):
                        for (
                            other_par_id,
                            other_syms,
                        ) in cell.directional_parents.items():
                            if syms == {flow_.fake_edge_sym} and other_syms == {
                                flow_.fake_edge_sym
                            }:
                                continue
                            other_parent = cells().from_id(other_par_id)
                            if other_parent.position <= parent.position:
                                continue
                            if syms <= other_syms or flow_.fake_edge_sym in other_syms:
                                should_skip = True
                                break
                    if should_skip:
                        continue
                    this_cell_parents.add(par_id)
            self.cell_parents[cell.id] = this_cell_parents
        for cell_id, parents in self.cell_parents.items():
            for parent_id in parents:
                self.cell_children.setdefault(parent_id, set()).add(cell_id)
        for cell in cells_to_check:
            self.cell_children.setdefault(cell.cell_id, set())

    def compute_frontend_checker_result(
        self,
        cells_to_check: Optional[Iterable[Cell]] = None,
        update_liveness_time_versions: bool = False,
        last_executed_cell_id: Optional[IdType] = None,
    ) -> "FrontendCheckerResult":
        flow_ = flow()
        if last_executed_cell_id is None:
            last_executed_cell_id = flow_.last_executed_cell_id
        waiting_symbols_by_cell_id: Dict[IdType, Set[Symbol]] = {}
        killing_cell_ids_for_symbol: Dict[Symbol, Set[IdType]] = defaultdict(set)
        phantom_cell_info: Dict[IdType, Dict[IdType, Set[int]]] = {}
        checker_results_by_cid: Dict[IdType, CheckerResult] = {}
        last_executed_cell_pos = self._get_last_executed_pos_and_handle_reactive_tags(
            last_executed_cell_id
        )
        if cells_to_check is None:
            cells_to_check = cells().current_cells_for_each_id()
        cells_to_check = sorted(cells_to_check, key=lambda c: c.position)
        for cell in cells_to_check:
            checker_result = self._check_one_cell(
                cell,
                update_liveness_time_versions,
                last_executed_cell_pos,
                waiting_symbols_by_cell_id,
                killing_cell_ids_for_symbol,
                phantom_cell_info,
            )
            if checker_result is not None:
                checker_results_by_cid[cell.cell_id] = checker_result

        self._compute_dag_based_waiters(cells_to_check)
        if last_executed_cell_pos is not None:
            self._compute_reactive_cells_for_reactive_symbols(
                checker_results_by_cid, last_executed_cell_pos
            )
        self._compute_ready_making_cells(
            waiting_symbols_by_cell_id,
            killing_cell_ids_for_symbol,
            last_executed_cell_id,
        )
        self._compute_waiter_and_ready_maker_links()
        if flow_.mut_settings.lint_out_of_order_usages:
            self._compute_unsafe_order_usages(cells_to_check)
        self._compute_filtered_parents(cells_to_check)
        return self
