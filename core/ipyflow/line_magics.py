# -*- coding: utf-8 -*-
import ast
import inspect
import json
import os.path
import re
import shlex
import sys
from typing import TYPE_CHECKING, Iterable, Optional, Sequence, Type, cast

import pyccolo as pyc
from IPython import get_ipython
from IPython.core.magic import register_line_magic

from ipyflow.analysis.symbol_ref import SymbolRef
from ipyflow.annotations.compiler import (
    register_annotations_directory,
    register_annotations_file,
)
from ipyflow.config import (
    ExecutionMode,
    ExecutionSchedule,
    FlowDirection,
    Highlights,
    ReactivityMode,
)
from ipyflow.data_model.cell import cells
from ipyflow.data_model.symbol import Symbol
from ipyflow.experimental.dag import create_dag_metadata
from ipyflow.singletons import flow, shell
from ipyflow.slicing.mixin import SliceableMixin, format_slice
from ipyflow.tracing.symbol_resolver import resolve_rval_symbols
from ipyflow.utils.magic_parser import MagicParser

if TYPE_CHECKING:
    from ipyflow.flow import NotebookFlow


_FLOW_LINE_MAGIC = "flow"


# TODO: update this
_USAGE = """Options:
[enable|disable]
    - Toggle dataflow capture. On by default.

[deps|show_deps|show_dependencies] <symbol>: 
    - This will print out the dependencies for given symbol.

[code|get_code] <symbol>: 
    - This will print the backward slice for the given symbol.

[waiting|show_waiting]: 
    - This will print out all the global variables that are waiting for newer dependencies. 

slice <cell_num>:
    - This will print the code necessary to reconstruct <cell_num> using a dynamic
      program slicing algorithm.
      
tag <tag>:
    - This will tag the executing cell with the given tag.
      
show_tags:
    - This will display the current tags of the executing cell.
    
register_annotations <directory_or_file>:
    - This will register the annotations in the given directory or file.
""".strip()


print_ = print  # to keep the test from failing since this is a legitimate print


def warn(*args, **kwargs):
    print_(*args, file=sys.stderr, **kwargs)


def make_line_magic(flow_: "NotebookFlow"):
    line_magic_names = [
        name for name, val in globals().items() if inspect.isfunction(val)
    ]

    def _handle(cmd, line):
        cmd = cmd.replace("-", "_")
        if cmd in ("enable", "disable", "on", "off"):
            return toggle_dataflow(cmd)
        elif cmd in ("deps", "show_deps", "show_dependency", "show_dependencies"):
            return show_deps(line)
        elif cmd in ("code", "get_code"):
            return get_code(line)
        elif cmd in ("waiting", "show_waiting"):
            return show_waiting(line)
        elif cmd == "trace_messages":
            return trace_messages(line)
        elif cmd in ("hls", "nohls", "highlight", "highlights"):
            return set_highlights(cmd, line)
        elif cmd in ("dag", "make_dag", "cell_dag", "make_cell_dag"):
            return json.dumps(create_dag_metadata(), indent=2)
        elif cmd in ("slice", "make_slice", "gather_slice"):
            return make_slice(line)
        elif cmd == "tag":
            return tag(line)
        elif cmd == "show_tags":
            return show_tags(line)
        elif cmd in ("mode", "exec_mode"):
            return set_exec_mode(line)
        elif cmd in ("schedule", "exec_schedule", "execution_schedule"):
            return set_exec_schedule(line)
        elif cmd in (
            "direction",
            "flow_direction",
            "order",
            "flow_order",
            "semantics",
            "flow_semantics",
        ):
            return set_flow_direction(line)
        elif cmd == "reactivity":
            return set_reactivity(line)
        elif cmd in ("register", "register_tracer"):
            return register_tracer(line)
        elif cmd in ("deregister", "deregister_tracer"):
            return deregister_tracer(line)
        elif cmd == "clear":
            flow_.min_timestamp = flow_.cell_counter()
            return None
        elif cmd.endswith("warn_ooo"):
            flow_.mut_settings.warn_out_of_order_usages = not cmd.startswith("no")
            return None
        elif cmd.endswith("lint_ooo"):
            flow_.mut_settings.lint_out_of_order_usages = not cmd.startswith("no")
            return None
        elif cmd == "syntax_transforms":
            is_on = line.endswith(("enabled", "on"))
            is_off = line.endswith(("disabled", "off"))
            if is_on or is_off:
                flow_.mut_settings.syntax_transforms_enabled = is_on
            return None
        elif cmd == "syntax_transforms_only":
            flow_.mut_settings.syntax_transforms_only = True
            return None
        elif cmd.startswith("register_annotation"):
            return register_annotations(line)
        elif cmd == "toggle_reactivity":
            flow_.toggle_reactivity()
            return None
        elif cmd == "bump_min_forced_reactive_counter":
            flow_.bump_min_forced_reactive_counter()
            return None
        elif cmd in line_magic_names:
            warn(
                f"We have a magic for {cmd}, but have not yet registered it",
            )
            return None
        else:
            warn(_USAGE)
            return None

    def _flow_magic(line: str):
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

    def histslice(line: str) -> None:
        get_ipython().run_line_magic("flow", f"slice --noheader {line}")

    register_line_magic(histslice)

    # FIXME (smacke): probably not a great idea to rely on this
    _flow_magic.__name__ = _FLOW_LINE_MAGIC
    return register_line_magic(_flow_magic)


def toggle_dataflow(line: str) -> Optional[str]:
    usage = "Usage: %flow [enable|disable]"
    line = line.strip()
    flow_ = flow()
    if line in ("enable", "on"):
        flow_.mut_settings.dataflow_enabled = True
        flow_.mut_settings.syntax_transforms_only = False
        return "dataflow capture enabled"
    elif line in ("disable", "off"):
        flow_.mut_settings.dataflow_enabled = False
        return "dataflow capture disabled"
    else:
        warn(usage)
        return None


def show_deps(symbol_str: str) -> Optional[str]:
    usage = "Usage: %flow show_[deps|dependencies] <symbol>"
    if len(symbol_str) == 0:
        warn(usage)
        return None
    try:
        node = cast(ast.Expr, ast.parse(symbol_str).body[0]).value
    except SyntaxError:
        warn(f"Could not parse symbols from string {symbol_str.strip()}")
        return None
    if isinstance(node, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
        warn(usage)
        return None
    sym = SymbolRef.resolve(symbol_str)
    if sym is None:
        warn(
            f"Could not find symbol metadata for {symbol_str.strip()}",
        )
        return None
    parents = {par for par in sym.parents if par.is_user_accessible}
    children = {child for child in sym.children if child.is_user_accessible}
    sym_extra_info = f"defined cell: {sym.defined_cell_num}; last updated cell: {sym.timestamp.cell_num}"
    if sym.required_timestamp.is_initialized:
        sym_extra_info += f"; required: {sym.required_timestamp.cell_num}"
    return "Symbol {} ({}) is dependent on {} and is a parent of {}".format(
        sym.full_namespace_path,
        sym_extra_info,
        parents or "nothing",
        children or "nothing",
    )


def get_code(symbol_str: str) -> Optional[str]:
    usage = "Usage: %flow [get_]code <symbol>"
    if len(symbol_str) == 0:
        warn(usage)
        return None
    try:
        node = cast(ast.Expr, ast.parse(symbol_str).body[0]).value
    except SyntaxError:
        warn(f"Could not parse symbols from string {symbol_str.strip()}")
        return None
    if isinstance(node, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
        warn(usage)
        return None
    sym = SymbolRef.resolve(symbol_str)
    if sym is None:
        warn(
            f"Could not find unique symbol metadata for {symbol_str.strip()}",
        )
        return None
    return str(sym.code())


def show_waiting(line_: str) -> Optional[str]:
    usage = "Usage: %flow show_waiting [global|all]"
    line = line_.split()
    if len(line) == 0 or line[0] == "global":
        sym_sets: Iterable[Iterable[Symbol]] = [
            flow().global_scope.all_symbols_this_indentation()
        ]
    elif line[0] == "all":
        sym_sets = flow().aliases.values()
    else:
        warn(usage)
        return None
    waiter_set = set()
    for sym_set in sym_sets:
        for sym in sym_set:
            if sym.is_waiting and not sym.is_anonymous:
                waiter_set.add(sym)
    if not waiter_set:
        return "No symbol waiting on dependencies for now!"
    else:
        return "Symbol(s) waiting on dependencies: %s" % waiter_set


def trace_messages(line_: str) -> None:
    line = line_.split()
    usage = "Usage: %flow trace_messages [enable|disable]"
    if len(line) != 1:
        warn(usage)
        return
    setting = line[0].lower()
    if setting == "on" or setting.startswith("enable"):
        flow().trace_messages_enabled = True
    elif setting == "off" or setting.startswith("disable"):
        flow().trace_messages_enabled = False
    else:
        warn(usage)


def set_highlights(cmd: str, rest: str) -> None:
    usage = "Usage: %flow [hls [strategy]|nohls]"
    rest = rest.lower().strip()
    if cmd == "hls" or cmd != "nohls":
        if rest == "" or rest == "on" or rest.startswith("enable"):
            flow().mut_settings.highlights = Highlights.EXECUTED
        elif rest == "off" or rest.startswith("disable"):
            flow().mut_settings.highlights = Highlights.NONE
        elif rest in {member.value for member in Highlights}:
            flow().mut_settings.highlights = Highlights(rest)
        else:
            warn(usage)
    elif cmd == "nohls":
        flow().mut_settings.highlights = Highlights.NONE


_SLICE_PARSER = MagicParser("slice")
_SLICE_PARSER.add_argument("cell_num", nargs="?", type=int, default=None)
_SLICE_PARSER.add_argument("--stmt", "--stmts", action="store_true")
_SLICE_PARSER.add_argument("--blacken", action="store_true")
_SLICE_PARSER.add_argument("--tag", nargs="?", type=str, default=None)
_SLICE_PARSER.add_argument("--noheader", action="store_true")


def make_slice(line: str) -> Optional[str]:
    args = _SLICE_PARSER.parse_args(shlex.split(line))
    if args.help:
        return None
    tag = args.tag
    slice_cells = None
    cell_num = args.cell_num
    if cell_num is None:
        if tag is None:
            cell_num = cells().exec_counter() - 1
    if cell_num is not None:
        slice_cells = {cells().at_timestamp(cell_num)}
    elif args.tag is not None:
        if tag.startswith("$"):
            tag = tag[1:]
            cells().current_cell().mark_as_reactive_for_tag(tag)
        slice_cells = cells().from_tag(tag)
    if slice_cells is None:
        warn("Cell(s) have not yet been run")
    elif len(slice_cells) == 0 and tag is not None:
        warn(f"No cell(s) for tag: {tag}")
    else:
        if args.stmt:
            closure: Sequence[SliceableMixin] = cells().compute_multi_slice_stmts(
                slice_cells
            )
        else:
            closure = cells().make_multi_slice(slice_cells)
        return str(
            format_slice(
                cells().make_cell_dict_from_closure(closure),
                blacken=args.stmt or args.blacken,
                format_type=str,
                include_cell_headers=not args.noheader,
            )
        )
    return None


_TAG_PARSER = MagicParser(
    "tag", usage="Usage: %flow tag <tag_name> [--remove] [--cell cell_num]"
)
_TAG_PARSER.add_argument("tag_name", type=str)
_TAG_PARSER.add_argument("--remove", action="store_true")
_TAG_PARSER.add_argument("--cell", type=int, default=None)


def tag(line: str) -> None:
    args = _TAG_PARSER.parse_args(shlex.split(line))
    if args.help:
        return
    tag = args.tag_name
    if args.cell is None:
        cell = cells().current_cell()
    else:
        cell = cells().at_counter(args.cell)
    cell_tags = set(cell.tags)
    if args.remove:
        cell.tags = tuple(cell_tags - {tag})
    else:
        cell.tags = tuple(cell_tags | {tag})
        cells()._cells_by_tag[tag].add(cell)
    return None


_SHOW_TAGS_PARSER = MagicParser(
    "show_tags", usage="Usage: %flow show_tags [--cell cell_num]"
)
_SHOW_TAGS_PARSER.add_argument("--cell", type=int, default=None)


def show_tags(line: str) -> None:
    args = _SHOW_TAGS_PARSER.parse_args(shlex.split(line))
    if args.help:
        return
    if args.cell is None:
        cell = cells().current_cell()
    else:
        cell = cells().at_counter(args.cell)
    print_("Cell has tags:", cell.tags)
    return None


def set_exec_mode(line_: str) -> None:
    usage = f"Usage: %flow mode [{ExecutionMode.NORMAL}|{ExecutionMode.REACTIVE}]"
    try:
        exec_mode = ExecutionMode(line_.strip())
    except ValueError:
        warn(usage)
        return
    flow_ = flow()
    flow_.mut_settings.exec_mode = exec_mode
    if exec_mode == ExecutionMode.REACTIVE:
        for cell in cells().current_cells_for_each_id():
            cell.set_ready(False)
    flow_._min_new_ready_cell_counter = flow_.cell_counter() + 1
    comm = flow_.comm_manager._comm
    if comm is not None:
        comm.send(
            {"type": "set_exec_mode", "exec_mode": exec_mode.value, "success": True}
        )


def set_exec_schedule(line_: str) -> None:
    usage = f"Usage: %flow schedule [{'|'.join(schedule.value for schedule in ExecutionSchedule)}]"
    if line_.startswith("liveness"):
        schedule = ExecutionSchedule.LIVENESS_BASED
    elif line_.startswith("dag"):
        schedule = ExecutionSchedule.DAG_BASED
    elif line_.startswith("hybrid"):
        schedule = ExecutionSchedule.HYBRID_DAG_LIVENESS_BASED
    else:
        warn(usage)
        return
    flow().mut_settings.exec_schedule = schedule


def set_flow_direction(line_: str) -> None:
    line_ = line_.lower().strip()
    usage = (
        f"Usage: %flow direction [{FlowDirection.ANY_ORDER}|{FlowDirection.IN_ORDER}]"
    )
    if line_.startswith("any") or line_ in ("unordered", "both"):
        flow_order = FlowDirection.ANY_ORDER
    elif line_.startswith("in") or line_ in ("ordered", "linear"):
        flow_order = FlowDirection.IN_ORDER
    else:
        warn(usage)
        return
    flow().mut_settings.flow_order = flow_order


def set_reactivity(line_: str) -> None:
    line_ = line_.lower().strip()
    usage = (
        f"Usage: %flow reactivity [{ReactivityMode.BATCH}|{ReactivityMode.INCREMENTAL}]"
    )
    if line_ in ("batch", "incremental"):
        reactivity = ReactivityMode(line_)
    else:
        warn(usage)
        return
    flow().mut_settings.reactivity_mode = reactivity


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
        syms = resolve_rval_symbols(name, should_update_usage_info=False)
        if len(syms) == 1:
            return next(iter(syms)).obj
        else:
            return None


def _deregister_tracers(tracers):
    shell().tracer_cleanup_pending = True
    for tracer in tracers:
        tracer.clear_instance()
        try:
            shell().registered_tracers.remove(tracer)
        except ValueError:
            pass


def _deregister_tracers_for(tracer_cls):
    _deregister_tracers(
        [tracer_cls]
        + [
            tracer
            for tracer in shell().registered_tracers
            if tracer.__name__ == tracer_cls.__name__
        ]
    )


def register_tracer(line_: str) -> None:
    line_ = line_.strip()
    usage = "Usage: %flow register_tracer <module.path.to.tracer_class>"
    tracer_cls = _resolve_tracer_class(line_)
    if tracer_cls is None:
        warn(usage)
        return
    _deregister_tracers_for(tracer_cls)
    tracer_cls.instance()
    shell().registered_tracers.insert(0, tracer_cls)


def deregister_tracer(line_: str) -> None:
    line_ = line_.strip()
    usage = "Usage: %flow deregister_tracer [<module.path.to.tracer_class>|all]"
    if line_.lower() == "all":
        _deregister_tracers(list(shell().registered_tracers))
    else:
        tracer_cls = _resolve_tracer_class(line_)
        if tracer_cls is None:
            warn(usage)
            return
        _deregister_tracers_for(tracer_cls)


def register_annotations(line_: str) -> None:
    line_ = line_.strip()
    usage = "Usage: %flow register_annotations <directory_or_file>"
    if os.path.isdir(line_):
        modules = register_annotations_directory(line_)
    elif os.path.isfile(line_):
        modules = register_annotations_file(line_)
    else:
        warn(usage)
        return
    print_("Registered annotations for modules:", modules)
