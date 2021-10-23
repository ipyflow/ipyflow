# -*- coding: future_annotations -*-
import argparse
import ast
import astunparse
import logging
import shlex
from typing import cast, TYPE_CHECKING

from nbsafety.data_model.code_cell import cells
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.data_model.timestamp import Timestamp
from nbsafety.run_mode import FlowOrder, ExecutionMode, ExecutionSchedule
from nbsafety.singletons import nbs
from nbsafety.tracing.symbol_resolver import resolve_rval_symbols

if TYPE_CHECKING:
    from typing import Iterable, List, Optional, Set

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


USAGE = """Options:

[deps|show_deps|show_dependencies] <symbol_1>, <symbol_2> ...: 
    - This will print out the dependencies for given symbols.
      Multiple symbols should be separated with commas.

[stale|show_stale]: 
    - This will print out all the global variables that are stale. 

remove_dependency <parent_name> <child_name>:
    - This will remove the dependency between parent variable and the child variable.

slice <cell_num>:
    - This will print the code necessary to reconstruct <cell_num> using a dynamic
      program slicing algorithm.

add_dependency <parent_name> <child_name>:
    - This will add the dependency between parent variable and the child variable.

turn_off_warnings_for  <variable_name> <variable_name2> ...: 
    - This will turn off the warnings for given global variables. These variables will not be
      considered as stale anymore. Multiple variables should be separated with spaces.

turn_off_warnings_for  <variable_name> <variable_name2> ...: 
    - This will turn the warnings back on for given global variables. These variables could have
      stale dependencies now. Multiple variables should be separated with spaces."""


def show_deps(symbols: str) -> Optional[str]:
    usage = 'Usage: %safety show_[deps|dependencies] <symbol_1>[, <symbol_2> ...]'
    if len(symbols) == 0:
        logger.warning(usage)
        return None
    try:
        node = cast(ast.Expr, ast.parse(symbols).body[0]).value
    except SyntaxError:
        logger.warning('Could not find symbol metadata for %s', symbols)
        return None
    if isinstance(node, ast.Tuple):
        unresolved_symbols = node.elts
    else:
        unresolved_symbols = [node]
    statements = []
    for unresolved in unresolved_symbols:
        dsyms = resolve_rval_symbols(unresolved, should_update_usage_info=False)
        if len(dsyms) == 0:
            logger.warning('Could not find symbol metadata for %s', astunparse.unparse(unresolved).strip())
        for dsym in dsyms:
            parents = {par for par in dsym.parents if par.is_user_accessible}
            children = {child for child in dsym.children if child.is_user_accessible}
            dsym_extra_info = f'defined cell: {dsym.defined_cell_num}; last updated cell: {dsym.timestamp.cell_num}'
            if dsym.required_timestamp.is_initialized:
                dsym_extra_info += f'; required: {dsym.required_timestamp.cell_num}'
            statements.append(
                'Symbol {} ({}) is dependent on {} and is a parent of {}'.format(
                    dsym.full_namespace_path,
                    dsym_extra_info,
                    parents or 'nothing',
                    children or 'nothing',
                )
            )
    if len(statements) == 0:
        return None
    else:
        return '\n'.join(statements)


def show_stale(line_: str) -> Optional[str]:
    usage = 'Usage: %safety show_stale [global|all]'
    line = line_.split()
    if len(line) == 0 or line[0] == 'global':
        dsym_sets: Iterable[Iterable[DataSymbol]] = [nbs().global_scope.all_data_symbols_this_indentation()]
    elif line[0] == 'all':
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
        return 'No symbol has stale dependencies for now!'
    else:
        return 'Symbol(s) with stale dependencies are: %s' % stale_set


def trace_messages(line_: str) -> None:
    line = line_.split()
    usage = 'Usage: %safety trace_messages [enable|disable]'
    if len(line) != 1:
        logger.warning(usage)
        return
    setting = line[0].lower()
    if setting == 'on' or setting.startswith('enable'):
        nbs().trace_messages_enabled = True
    elif setting == 'off' or setting.startswith('disable'):
        nbs().trace_messages_enabled = False
    else:
        logger.warning(usage)


def set_highlights(cmd: str, rest: str) -> None:
    usage = 'Usage: %safety [hls|nohls]'
    if cmd == 'hls':
        nbs().mut_settings.highlights_enabled = True
    elif cmd == 'nohls':
        nbs().mut_settings.highlights_enabled = False
    else:
        rest = rest.lower()
        if rest == 'on' or rest.startswith('enable'):
            nbs().mut_settings.highlights_enabled = True
        elif rest == 'off' or rest.startswith('disable'):
            nbs().mut_settings.highlights_enabled = False
        else:
            logger.warning(usage)


_SLICE_PARSER = argparse.ArgumentParser('slice')
_SLICE_PARSER.add_argument('cell_num', nargs='?', type=int, default=None)
_SLICE_PARSER.add_argument('--stmt', '--stmts', action='store_true')
_SLICE_PARSER.add_argument('--tag', nargs='?', type=str, default=None)


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
        if tag.startswith('$'):
            tag = tag[1:]
            cells().current_cell().mark_as_reactive_for_tag(tag)
        slice_cells = cells().from_tag(tag)
    if slice_cells is None:
        logger.warning("Cell(s) have not yet been run")
    elif len(slice_cells) == 0 and tag is not None:
        logger.warning("No cell(s) for tag: %s", tag)
    else:
        deps = list(cells().compute_slice_for_cells(slice_cells, stmt_level=args.stmt).items())
        deps.sort()
        return '\n\n'.join(f'# Cell {cell_num}\n' + content for cell_num, content in deps)
    return None


def _find_symbols(syms: List[str]) -> List[DataSymbol]:
    results = []
    for sym in syms:
        result = nbs().global_scope.lookup_data_symbol_by_name(sym)
        if result is None:
            logger.warning('Could not find symbol metadata for %s', sym)
        results.append(result)
    return results


def remove_dep(line_: str) -> None:
    usage = 'Usage: %safety remove_dependency <parent_name> <child_name>'
    line = line_.split()
    if len(line) != 2:
        logger.warning(usage)
        return
    results = _find_symbols(line)
    if len(results) != len(line):
        return
    parent_data_sym, child_data_sym = results
    if parent_data_sym not in child_data_sym.parents:
        logger.warning('The two symbols do not have a dependency relation')
        return
    del parent_data_sym.children[child_data_sym]
    del child_data_sym.parents[parent_data_sym]


def add_dep(line_: str) -> None:
    usage = 'Usage: %safety add_dependency <parent_name> <child_name>'
    line = line_.split()
    if len(line) != 2:
        logger.warning(usage)
        return
    results = _find_symbols(line)
    if len(results) != len(line):
        return
    parent_data_sym, child_data_sym = results
    if parent_data_sym in child_data_sym.parents:
        logger.warning('The two symbols already have a dependency relation')
        return
    parent_data_sym.children[child_data_sym].append(Timestamp.current())
    child_data_sym.parents[parent_data_sym].append(Timestamp.current())


def turn_off_warnings_for(line_: str) -> None:
    usage = 'Usage: %safety turn_off_warnings_for <variable_name> <variable_name2> ...'
    line = line_.split()
    if len(line) <= 1:
        logger.warning(usage)
        return
    for data_sym_name in line:
        data_sym = nbs().global_scope.lookup_data_symbol_by_name(data_sym_name)
        if data_sym:
            data_sym.disable_warnings = True
            logger.warning('Warnings are turned off for %s', data_sym_name)
        else:
            logger.warning('Could not find symbol metadata for %s', data_sym_name)


def turn_on_warnings_for(line_: str) -> None:
    usage = 'Usage: %safety turn_on_warnings_for <variable_name> <variable_name2> ...'
    line = line_.split()
    if len(line) == 0:
        logger.warning(usage)
        return
    for data_sym_name in line:
        data_sym = nbs().global_scope.lookup_data_symbol_by_name(data_sym_name)
        if data_sym:
            data_sym.disable_warnings = False
            logger.warning('Warnings are turned on for %s', data_sym_name)
        else:
            logger.warning('Could not find symbol metadata for %s', data_sym_name)


def set_exec_mode(line_: str) -> None:
    usage = f'Usage: %safety mode [{ExecutionMode.NORMAL}|{ExecutionMode.REACTIVE}]'
    try:
        exec_mode = ExecutionMode(line_.strip())
    except ValueError:
        logger.warning(usage)
        return
    nbs().mut_settings.exec_mode = exec_mode


def set_exec_schedule(line_: str) -> None:
    usage = f'Usage: %safety schedule [{ExecutionSchedule.LIVENESS_BASED}|{ExecutionSchedule.DAG_BASED}|{ExecutionSchedule.STRICT}]'
    if line_.startswith('liveness'):
        schedule = ExecutionSchedule.LIVENESS_BASED
    elif line_.startswith('dag'):
        schedule = ExecutionSchedule.DAG_BASED
    elif line_.startswith('strict'):
        if nbs().mut_settings.flow_order != FlowOrder.IN_ORDER:
            logger.warning('Strict schedule only applicable for forward data flow; skipping')
            return
        schedule = ExecutionSchedule.STRICT
    else:
        logger.warning(usage)
        return
    nbs().mut_settings.exec_schedule = schedule


def set_flow_order(line_: str) -> None:
    line_ = line_.lower().strip()
    usage = f'Usage: %safety flow [{FlowOrder.ANY_ORDER}|{FlowOrder.IN_ORDER}]'
    if line_.startswith('any') or line_ in ('unordered', 'both'):
        flow_order = FlowOrder.ANY_ORDER
    elif line_.startswith('in') or line_ in ('ordered', 'linear'):
        flow_order = FlowOrder.IN_ORDER
    else:
        logger.warning(usage)
        return
    nbs().mut_settings.flow_order = flow_order
