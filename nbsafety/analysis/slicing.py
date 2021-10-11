# -*- coding: future_annotations -*-
import ast
import astunparse
import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from nbsafety.data_model.timestamp import Timestamp
from nbsafety.singletons import nbs

if TYPE_CHECKING:
    from typing import Dict, List, Set
    from nbsafety.data_model.code_cell import ExecutedCodeCell
    from nbsafety.types import TimestampOrCounter


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


def _get_ts_dependencies(
    timestamp: TimestampOrCounter,
    dependencies: Set[TimestampOrCounter],
    timestamp_to_ts_deps: Dict[TimestampOrCounter, Set[TimestampOrCounter]],
) -> None:
    """
    For a given timestamps, this function recursively populates a set of
    timestamps that the given timestamp depends on, based on the live symbols.

    Args:
        - timestamp (ts_or_int): current timestamp / cell to get dependencies for
        - dependencies (set<ts_or_int>): set of timestamps / cell coutners so far that exist
        - cell_num_to_dynamic_deps (dict<ts_or_int, set<ts_or_int>>): mapping from used timestamp
        to timestamp of symbol definition
        - timestamp_to_static_ts_deps (dict<ts_or_int, set<ts_or_int>>): mapping from used timestamp
        to timestamp of symbol definition (for statically computed timestamps)

    Returns:
        None
    """
    # Base case: cell already in dependencies
    if timestamp in dependencies:
        return
    if isinstance(timestamp, int) and timestamp < 0:
        return
    if isinstance(timestamp, Timestamp) and not timestamp.is_initialized:
        return

    # Add current cell to dependencies
    dependencies.add(timestamp)

    # Retrieve cell numbers for the dependent symbols
    # Add dynamic and static dependencies
    dep_timestamps = timestamp_to_ts_deps[timestamp]
    logger.info('dynamic ts deps for %s: %s', timestamp, dep_timestamps)

    # For each dependent cell, recursively get their dependencies
    for ts in dep_timestamps - dependencies:
        _get_ts_dependencies(ts, dependencies, timestamp_to_ts_deps)


def _coarsen_timestamps(graph: Dict[Timestamp, Set[Timestamp]]) -> Dict[int, Set[int]]:
    coarsened: Dict[int, Set[int]] = defaultdict(set)
    for child, parents in graph.items():
        for par in parents:
            coarsened[child.cell_num].add(par.cell_num)
    return coarsened


def _graph_union(
    graph: Dict[TimestampOrCounter, Set[TimestampOrCounter]],
    subsumed: Dict[TimestampOrCounter, Set[TimestampOrCounter]],
):
    for child, parents in subsumed.items():
        graph[child] |= parents
    return graph


def _compute_slice_impl(seed_ts: TimestampOrCounter) -> Set[TimestampOrCounter]:
    dependencies: Set[TimestampOrCounter] = set()
    timestamp_to_ts_deps: Dict[TimestampOrCounter, Set[TimestampOrCounter]] = defaultdict(set)
    if nbs().mut_settings.dynamic_slicing_enabled:
        if isinstance(seed_ts, Timestamp):
            timestamp_to_ts_deps = _graph_union(timestamp_to_ts_deps, nbs().dynamic_data_deps)
        else:
            timestamp_to_ts_deps = _graph_union(timestamp_to_ts_deps, _coarsen_timestamps(nbs().dynamic_data_deps))
    if nbs().mut_settings.static_slicing_enabled:
        if isinstance(seed_ts, Timestamp):
            timestamp_to_ts_deps = _graph_union(timestamp_to_ts_deps, nbs().static_data_deps)
        else:
            timestamp_to_ts_deps = _graph_union(timestamp_to_ts_deps, _coarsen_timestamps(nbs().static_data_deps))

    # ensure we at least get the static deps
    _get_ts_dependencies(seed_ts, dependencies, timestamp_to_ts_deps)
    if isinstance(seed_ts, Timestamp):
        for ts in list(timestamp_to_ts_deps.keys()):
            if ts.cell_num == seed_ts.cell_num:
                _get_ts_dependencies(ts, dependencies, timestamp_to_ts_deps)
    return dependencies


class CodeCellSlicingMixin:
    def compute_slice(  # type: ignore
        self: ExecutedCodeCell, stmt_level: bool = False
    ) -> Dict[int, str]:
        """
        Gets a dictionary object of cell dependencies for the cell with
        the specified execution counter.

        Args:
            - cell_num (int): cell to get dependencies for, defaults to last
                execution counter

        Returns:
            - dict (int, str): map from required cell number to code
                representing dependencies
        """
        if stmt_level:
            stmts_by_cell_num = self.compute_slice_stmts()
            stmts_by_cell_num.pop(self.cell_ctr, None)
            ret = {
                ctr: '\n'.join(astunparse.unparse(stmt).strip() for stmt in stmts)
                for ctr, stmts in stmts_by_cell_num.items()
            }
            ret[self.cell_ctr] = self.content
            return ret
        else:
            deps: Set[int] = _compute_slice_impl(self.cell_ctr)
            return {dep: self.from_timestamp(dep).content for dep in deps}

    def compute_slice_stmts(  # type: ignore
        self: ExecutedCodeCell,
    ) -> Dict[int, List[ast.stmt]]:
        deps_stmt: Set[Timestamp] = _compute_slice_impl(Timestamp(self.cell_ctr, -1))
        stmts_by_cell_num = defaultdict(list)
        seen_stmt_ids = set()
        for ts in sorted(deps_stmt):
            if ts.cell_num > self.cell_ctr:
                break
            stmt = self.from_timestamp(ts.cell_num).to_ast().body[ts.stmt_num]
            stmt_id = id(stmt)
            if stmt is None or stmt_id in seen_stmt_ids:
                continue
            seen_stmt_ids.add(stmt_id)
            if stmt is not None:
                stmts_by_cell_num[ts.cell_num].append(stmt)
        stmts_by_cell_num[self.cell_ctr] = list(self.to_ast().body)
        return dict(stmts_by_cell_num)
