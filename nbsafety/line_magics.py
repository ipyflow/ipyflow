# -*- coding: utf-8 -*-
import argparse
import ast
import astunparse
import importlib
import logging
import shlex
from typing import cast, Iterable, List, Optional, Type

from IPython import get_ipython
import pyccolo as pyc

from nbsafety.data_model.code_cell import cells
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.data_model.timestamp import Timestamp
from nbsafety.run_mode import FlowOrder, ExecutionMode, ExecutionSchedule
from nbsafety.singletons import nbs
from nbsafety.tracing.symbol_resolver import resolve_rval_symbols

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


USAGE = """Options:

[deps|show_deps|show_dependencies] <symbol_1>, <symbol_2> ...: 
    - This will print out the dependencies for given symbols.
      Multiple symbols should be separated with commas.

[stale|show_stale]: 
    - This will print out all the global variables that are stale. 

slice <cell_num>:
    - This will print the code necessary to reconstruct <cell_num> using a dynamic
      program slicing algorithm."""


def show_deps(symbols: str) -> Optional[str]:
    usage = "Usage: %safety show_[deps|dependencies] <symbol_1>[, <symbol_2> ...]"
    if len(symbols) == 0:
        logger.warning(usage)
        return None
    try:
        node = cast(ast.Expr, ast.parse(symbols).body[0]).value
    except SyntaxError:
        logger.warning("Could not find symbol metadata for %s", symbols)
        return None
    if isinstance(node, ast.Tuple):
        unresolved_symbols = node.elts
    else:
        unresolved_symbols = [node]
    statements = []
    for unresolved in unresolved_symbols:
        dsyms = resolve_rval_symbols(unresolved, should_update_usage_info=False)
        if len(dsyms) == 0:
            logger.warning(
                "Could not find symbol metadata for %s",
                astunparse.unparse(unresolved).strip(),
            )
        for dsym in dsyms:
            parents = {par for par in dsym.parents if par.is_user_accessible}
            children = {child for child in dsym.children if child.is_user_accessible}
            dsym_extra_info = f"defined cell: {dsym.defined_cell_num}; last updated cell: {dsym.timestamp.cell_num}"
            if dsym.required_timestamp.is_initialized:
                dsym_extra_info += f"; required: {dsym.required_timestamp.cell_num}"
            statements.append(
                "Symbol {} ({}) is dependent on {} and is a parent of {}".format(
                    dsym.full_namespace_path,
                    dsym_extra_info,
                    parents or "nothing",
                    children or "nothing",
                )
            )
    if len(statements) == 0:
        return None
    else:
        return "\n".join(statements)


def show_stale(line_: str) -> Optional[str]:
    usage = "Usage: %safety show_stale [global|all]"
    line = line_.split()
    if len(line) == 0 or line[0] == "global":
        dsym_sets: Iterable[Iterable[DataSymbol]] = [
            nbs().global_scope.all_data_symbols_this_indentation()
        ]
    elif line[0] == "all":
        dsym_sets = nbs().aliases.values()
    else:
        logger.warning(usage)
        return None
    stale_set = set()
    for dsym_set in dsym_sets:
        for data_sym in dsym_set:
            if data_sym.is_stale and not data_sym.is_anonymous:
                stale_set.add(data_sym)
    if not stale_set:
        return "No symbol has stale dependencies for now!"
    else:
        return "Symbol(s) with stale dependencies are: %s" % stale_set


def trace_messages(line_: str) -> None:
    line = line_.split()
    usage = "Usage: %safety trace_messages [enable|disable]"
    if len(line) != 1:
        logger.warning(usage)
        return
    setting = line[0].lower()
    if setting == "on" or setting.startswith("enable"):
        nbs().trace_messages_enabled = True
    elif setting == "off" or setting.startswith("disable"):
        nbs().trace_messages_enabled = False
    else:
        logger.warning(usage)


def set_highlights(cmd: str, rest: str) -> None:
    usage = "Usage: %safety [hls|nohls]"
    if cmd == "hls":
        nbs().mut_settings.highlights_enabled = True
    elif cmd == "nohls":
        nbs().mut_settings.highlights_enabled = False
    else:
        rest = rest.lower()
        if rest == "on" or rest.startswith("enable"):
            nbs().mut_settings.highlights_enabled = True
        elif rest == "off" or rest.startswith("disable"):
            nbs().mut_settings.highlights_enabled = False
        else:
            logger.warning(usage)


_SLICE_PARSER = argparse.ArgumentParser("slice")
_SLICE_PARSER.add_argument("cell_num", nargs="?", type=int, default=None)
_SLICE_PARSER.add_argument("--stmt", "--stmts", action="store_true")
_SLICE_PARSER.add_argument("--tag", nargs="?", type=str, default=None)


def make_slice(line: str) -> Optional[str]:
    try:
        args = _SLICE_PARSER.parse_args(shlex.split(line))
    except:
        return None
    tag = args.tag
    slice_cells = None
    cell_num = args.cell_num
    if cell_num is None:
        if tag is None:
            cell_num = cells().exec_counter() - 1
    if cell_num is not None:
        slice_cells = {cells().from_timestamp(cell_num)}
    elif args.tag is not None:
        if tag.startswith("$"):
            tag = tag[1:]
            cells().current_cell().mark_as_reactive_for_tag(tag)
        slice_cells = cells().from_tag(tag)
    if slice_cells is None:
        logger.warning("Cell(s) have not yet been run")
    elif len(slice_cells) == 0 and tag is not None:
        logger.warning("No cell(s) for tag: %s", tag)
    else:
        deps = list(
            cells().compute_slice_for_cells(slice_cells, stmt_level=args.stmt).items()
        )
        deps.sort()
        return "\n\n".join(
            f"# Cell {cell_num}\n" + content for cell_num, content in deps
        )
    return None


def set_exec_mode(line_: str) -> None:
    usage = f"Usage: %safety mode [{ExecutionMode.NORMAL}|{ExecutionMode.REACTIVE}]"
    try:
        exec_mode = ExecutionMode(line_.strip())
    except ValueError:
        logger.warning(usage)
        return
    nbs().mut_settings.exec_mode = exec_mode


def set_exec_schedule(line_: str) -> None:
    usage = f"Usage: %safety schedule [{ExecutionSchedule.LIVENESS_BASED}|{ExecutionSchedule.DAG_BASED}|{ExecutionSchedule.STRICT}]"
    if line_.startswith("liveness"):
        schedule = ExecutionSchedule.LIVENESS_BASED
    elif line_.startswith("dag"):
        schedule = ExecutionSchedule.DAG_BASED
    elif line_.startswith("strict"):
        if nbs().mut_settings.flow_order != FlowOrder.IN_ORDER:
            logger.warning(
                "Strict schedule only applicable for forward data flow; skipping"
            )
            return
        schedule = ExecutionSchedule.STRICT
    else:
        logger.warning(usage)
        return
    nbs().mut_settings.exec_schedule = schedule


def set_flow_order(line_: str) -> None:
    line_ = line_.lower().strip()
    usage = f"Usage: %safety flow [{FlowOrder.ANY_ORDER}|{FlowOrder.IN_ORDER}]"
    if line_.startswith("any") or line_ in ("unordered", "both"):
        flow_order = FlowOrder.ANY_ORDER
    elif line_.startswith("in") or line_ in ("ordered", "linear"):
        flow_order = FlowOrder.IN_ORDER
    else:
        logger.warning(usage)
        return
    nbs().mut_settings.flow_order = flow_order


def _resolve_tracer_class(name: str) -> Optional[Type[pyc.BaseTracer]]:
    if "." in name:
        try:
            return pyc.resolve_tracer(name)
        except ImportError:
            return None
    else:
        tracer_cls = get_ipython().ns_table["user_global"].get(name, None)
        if tracer_cls is not None:
            return tracer_cls
        dsyms = resolve_rval_symbols(name, should_update_usage_info=False)
        if len(dsyms) == 1:
            return next(iter(dsyms)).obj
        else:
            return None


def _deregister_tracers(tracers):
    nbs().tracer_cleanup_pending = True
    for tracer in tracers:
        tracer.clear_instance()
        try:
            nbs().registered_tracers.remove(tracer)
        except ValueError:
            pass


def _deregister_tracers_for(tracer_cls):
    _deregister_tracers(
        [tracer_cls]
        + [
            tracer
            for tracer in nbs().registered_tracers
            if tracer.__name__ == tracer_cls.__name__
        ]
    )


def register_tracer(line_: str) -> None:
    line_ = line_.strip()
    usage = f"Usage: %safety register_tracer <module.path.to.tracer_class>"
    tracer_cls = _resolve_tracer_class(line_)
    if tracer_cls is None:
        logger.warning(usage)
        return
    _deregister_tracers_for(tracer_cls)
    tracer_cls.instance()
    nbs().registered_tracers.insert(0, tracer_cls)


def deregister_tracer(line_: str) -> None:
    line_ = line_.strip()
    usage = f"Usage: %safety deregister_tracer [<module.path.to.tracer_class>|all]"
    if line_.lower() == "all":
        _deregister_tracers(list(nbs().registered_tracers))
    else:
        tracer_cls = _resolve_tracer_class(line_)
        if tracer_cls is None:
            logger.warning(usage)
            return
        _deregister_tracers_for(tracer_cls)
