# -*- coding: utf-8 -*-
import ast
import logging
import textwrap
from collections import defaultdict
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Type, TypeVar

import black
from ipywidgets import HTML

from ipyflow.config import Interface
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.models import cells, stmts
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


FormatType = TypeVar("FormatType", HTML, str)


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
        - dependencies (set<ts_or_int>): set of timestamps / cell counters so far that exist
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
    for _ in flow().mut_settings.iter_dep_contexts():
        if isinstance(seeds[0], Timestamp):
            timestamp_to_ts_deps = _graph_union(timestamp_to_ts_deps, flow().data_deps)
        else:
            timestamp_to_ts_deps = _graph_union(
                timestamp_to_ts_deps, _coarsen_timestamps(flow().data_deps)
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


def format_slice(
    slice: Dict[int, str],
    blacken: bool = True,
    format_type: Optional[Type[FormatType]] = None,
) -> FormatType:
    iface = flow().mut_settings.interface
    if format_type is None:
        if iface in (Interface.IPYTHON, Interface.UNKNOWN):
            format_type = str
        else:
            format_type = HTML
    assert format_type is not None
    if blacken:
        for cell_num, content in list(slice.items()):
            try:
                slice[cell_num] = black.format_str(
                    content, mode=black.FileMode()
                ).strip()
            except Exception as e:
                logger.info("call to black failed with exception: %s", e)
    slice_text_cells = "\n\n".join(
        f"# Cell {cell_num}\n" + content for cell_num, content in sorted(slice.items())
    )
    if format_type is str:
        return slice_text_cells
    slice_text_linked_cells = []
    if iface == Interface.JUPYTER:
        container_selector = "javascript:document.getElementById('notebook-container')"
    elif iface == Interface.JUPYTERLAB:
        container_selector = (
            "javascript:document.getElementById("
            "document.querySelector('.jp-mod-current').dataset.id).children[2]"
        )
    else:
        container_selector = None
    for cell_num, content in sorted(slice.items()):
        cell = cells().at_counter(cell_num)
        if (
            container_selector is not None
            and cell.is_current_for_id
            and cell.position >= 0
        ):
            rendered_cell = (
                f'# <a href="{container_selector}.children[{cell.position}].scrollIntoView()">'
                f"Cell {cell_num}</a>"
            )
        else:
            rendered_cell = f"# Cell {cell_num}"
        slice_text_linked_cells.append(rendered_cell + f"\n{content}")
    assert format_type is HTML
    slice_text_no_cells = "\n".join(
        content for _cell_num, content in sorted(slice.items())
    )
    if blacken:
        slice_text_no_cells = black.format_str(
            slice_text_no_cells, mode=black.FileMode()
        ).strip()
    if iface == Interface.JUPYTER:
        classes = "output_subarea output_text output_stream output_stdout"
    elif iface == Interface.JUPYTERLAB:
        classes = "lm-Widget p-Widget jp-RenderedText jp-OutputArea-output"
    else:
        classes = ""
    return HTML(
        textwrap.dedent(
            f"""
        <div class="{classes}">
        <pre>
        <a href="javascript:navigator.clipboard.writeText('{slice_text_no_cells.encode("unicode_escape").decode("utf-8")}')">Copy code</a>\
 | <a href="javascript:navigator.clipboard.writeText('{slice_text_cells.encode("unicode_escape").decode("utf-8")}')">Copy cells</a>
 
        {{code}}
        </pre>
        </div>
        """
        ).format(code="\n\n".join(slice_text_linked_cells))
    )


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
            return {dep: cls.at_timestamp(dep).sanitized_content() for dep in deps}

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
            if ts.stmt_num == -1:
                continue
            stmt = stmts().module_stmt_node_at_timestamp(ts, include_extra=True)
            stmt_id = id(stmt)
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
