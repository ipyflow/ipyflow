# -*- coding: future_annotations -*-
import ast
import astunparse
import logging
from typing import cast, TYPE_CHECKING

from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.ipython_utils import CellNotRunYetError
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
            children: Set[DataSymbol] = set()
            children = children.union(
                *({child for child in children if child.is_user_accessible}
                  for children in dsym.children_by_cell_position.values())
            )
            if dsym.required_timestamp > 0:
                dsym_extra_info = 'last updated {}; required {}'.format(dsym.timestamp, dsym.required_timestamp)
            else:
                dsym_extra_info = 'defined in cell {}'.format(dsym.timestamp)
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


def make_slice(line: str) -> Optional[str]:
    usage = 'Usage: %safety slice <cell_num>'
    try:
        cell_num = int(line)
    except:
        logger.warning(usage)
        return None
    try:
        deps = list(nbs().get_cell_dependencies(cell_num).items())
        deps.sort()
        return '\n\n'.join(f'# Cell {cell_num}\n' + content for cell_num, content in deps)
    except CellNotRunYetError:
        logger.warning("Cell %d has not yet been run", cell_num)
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
    for children in parent_data_sym.children_by_cell_position.values():
        children.remove(child_data_sym)
    child_data_sym.parents.remove(parent_data_sym)


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
    parent_data_sym.children_by_cell_position[-1].add(child_data_sym)
    child_data_sym.parents.add(parent_data_sym)


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
