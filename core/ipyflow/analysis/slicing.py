# -*- coding: utf-8 -*-
import ast
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Type

import black

from ipyflow.data_model.timestamp import Timestamp
from ipyflow.singletons import flow
from ipyflow.types import TimestampOrCounter

if TYPE_CHECKING:
    import astunparse
elif hasattr(ast, "unparse"):
    astunparse = ast
else:
    import astunparse

if TYPE_CHECKING:
    from ipyflow.data_model.code_cell import CodeCell


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


def _get_ts_dependencies(
    timestamp: TimestampOrCounter,
    dependencies: Set[TimestampOrCounter],
    timestamp_to_ts_deps: Dict[TimestampOrCounter, Set[TimestampOrCounter]],
    timestamp_to_static_ts_deps: Dict[TimestampOrCounter, Set[TimestampOrCounter]],
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
    if (
        len(dep_timestamps) == 0
        or isinstance(timestamp, int)
        or not flow().mut_settings.dynamic_slicing_enabled
    ):
        static_dep_timestamps = timestamp_to_static_ts_deps[timestamp]
    else:
        static_dep_timestamps = set()

    # For each dependent cell, recursively get their dependencies
    for ts in (dep_timestamps | static_dep_timestamps) - dependencies:
        _get_ts_dependencies(
            ts, dependencies, timestamp_to_ts_deps, timestamp_to_static_ts_deps
        )


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


def compute_slice_impl(
    seeds: List[TimestampOrCounter], match_seed_stmts: bool = False
) -> Set[TimestampOrCounter]:
    assert len(seeds) > 0
    dependencies: Set[TimestampOrCounter] = set()
    timestamp_to_ts_deps: Dict[
        TimestampOrCounter, Set[TimestampOrCounter]
    ] = defaultdict(set)
    timestamp_to_static_ts_deps: Dict[
        TimestampOrCounter, Set[TimestampOrCounter]
    ] = defaultdict(set)
    if flow().mut_settings.dynamic_slicing_enabled:
        if isinstance(seeds[0], Timestamp):
            timestamp_to_ts_deps = _graph_union(
                timestamp_to_ts_deps, flow().dynamic_data_deps
            )
        else:
            timestamp_to_ts_deps = _graph_union(
                timestamp_to_ts_deps, _coarsen_timestamps(flow().dynamic_data_deps)
            )
    if flow().mut_settings.static_slicing_enabled:
        if isinstance(seeds[0], Timestamp):
            timestamp_to_static_ts_deps = _graph_union(
                timestamp_to_static_ts_deps, flow().static_data_deps
            )
        else:
            timestamp_to_static_ts_deps = _graph_union(
                timestamp_to_static_ts_deps,
                _coarsen_timestamps(flow().static_data_deps),
            )

    # ensure we at least get the static deps
    for seed in seeds:
        _get_ts_dependencies(
            seed, dependencies, timestamp_to_ts_deps, timestamp_to_static_ts_deps
        )
    if isinstance(seeds[0], Timestamp):
        for seed in seeds:
            for ts in list(
                timestamp_to_ts_deps.keys() | timestamp_to_static_ts_deps.keys()
            ):
                if ts.cell_num == seed.cell_num and (
                    not match_seed_stmts or ts.stmt_num == seed.stmt_num
                ):
                    _get_ts_dependencies(
                        ts,
                        dependencies,
                        timestamp_to_ts_deps,
                        timestamp_to_static_ts_deps,
                    )
    return dependencies


def make_slice_text(slice: Dict[int, str], blacken: bool = True) -> str:
    slice_text = "\n\n".join(
        f"# Cell {cell_num}\n" + content for cell_num, content in sorted(slice.items())
    )
    if blacken:
        try:
            slice_text = black.format_str(slice_text, mode=black.FileMode())
        except Exception as e:
            logger.info("call to black failed with exception: %s", e)
    return slice_text


class CodeCellSlicingMixin:
    def compute_slice(  # type: ignore
        self: "CodeCell", stmt_level: bool = False
    ) -> Dict[int, str]:
        return self.compute_slice_for_cells({self}, stmt_level=stmt_level)

    @staticmethod
    def _strip_tuple_parens(node: ast.AST, text: str) -> str:
        if (
            isinstance(node, (ast.BinOp, ast.Tuple))
            and len(text) >= 2
            and text[0] == "("
            and text[-1] == ")"
        ):
            return text[1:-1]
        else:
            return text

    @classmethod
    def _unparse(cls, stmt: ast.stmt) -> str:
        if isinstance(stmt, ast.Assign) and stmt.lineno == max(
            getattr(nd, "lineno", stmt.lineno) for nd in ast.walk(stmt)
        ):
            components = []
            for node in stmt.targets + [stmt.value]:
                components.append(astunparse.unparse(node).strip())
                components[-1] = cls._strip_tuple_parens(node, components[-1])
            return " = ".join(components)
        else:
            return astunparse.unparse(stmt)

    @classmethod
    def get_stmt_text(  # type: ignore
        cls: Type["CodeCell"],
        stmts_by_cell_num: Dict[int, List[ast.stmt]],
    ) -> Dict[int, str]:
        return {
            ctr: "\n".join(cls._unparse(stmt).strip() for stmt in stmts)
            for ctr, stmts in stmts_by_cell_num.items()
        }

    @classmethod
    def compute_slice_for_cells(  # type: ignore
        cls: Type["CodeCell"],
        cells: Set["CodeCell"],
        stmt_level: bool = False,
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
            stmts_by_cell_num = cls.compute_slice_stmts_for_cells(cells)
            for cell in cells:
                stmts_by_cell_num.pop(cell.cell_ctr, None)
            ret = cls.get_stmt_text(stmts_by_cell_num)
            for cell in cells:
                ret[cell.cell_ctr] = cell.sanitized_content()
            return ret
        else:
            deps: Set[int] = compute_slice_impl([cell.cell_ctr for cell in cells])
            return {dep: cls.from_timestamp(dep).sanitized_content() for dep in deps}

    def compute_slice_stmts(  # type: ignore
        self: "CodeCell",
    ) -> Dict[int, List[ast.stmt]]:
        return self.compute_slice_stmts_for_cells({self})

    @classmethod
    def compute_slice_stmts_for_timestamps(  # type: ignore
        cls: Type["CodeCell"],
        timestamps: Set[Timestamp],
        cells: Optional[Set["CodeCell"]] = None,
    ) -> Dict[int, List[ast.stmt]]:
        stmts_by_cell_num = defaultdict(list)
        seen_stmt_ids = set()
        for ts in sorted(timestamps):
            cell = cls.from_timestamp(ts)
            cell_stmts = cell.to_ast().body + [cell._extra_stmt]
            if ts.stmt_num < len(cell_stmts):
                stmt = cell_stmts[ts.stmt_num]
                stmt_id = id(stmt)
            else:
                stmt = stmt_id = None
            if stmt is None or stmt_id in seen_stmt_ids:
                continue
            seen_stmt_ids.add(stmt_id)
            if stmt is not None:
                stmts_by_cell_num[ts.cell_num].append(stmt)
        for cell in cells or []:
            stmts_by_cell_num[cell.cell_ctr] = list(cell.to_ast().body)
        return dict(stmts_by_cell_num)

    @classmethod
    def compute_slice_stmts_for_cells(  # type: ignore
        cls: Type["CodeCell"],
        cells: Set["CodeCell"],
    ) -> Dict[int, List[ast.stmt]]:
        deps_stmt: Set[Timestamp] = compute_slice_impl(
            [Timestamp(cell.cell_ctr, -1) for cell in cells]
        )
        return cls.compute_slice_stmts_for_timestamps(deps_stmt, cells=cells)
