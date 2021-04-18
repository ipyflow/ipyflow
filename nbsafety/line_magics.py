# -*- coding: future_annotations -*-
import ast
import astunparse
from typing import cast, TYPE_CHECKING
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.singletons import nbs
from nbsafety.tracing.symbol_resolver import resolve_rval_symbols

if TYPE_CHECKING:
    from typing import Iterable, List


USAGE = """Options:

show_[deps|dependencies] <symbol_1>, <symbol_2> ...: 
    - This will print out the dependencies for given symbols.
      Multiple symbols should be separated with commas.

show_stale: 
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


def show_deps(symbols: str):
    usage = 'Usage: %safety show_[deps|dependencies] <symbol_1>[, <symbol_2> ...]'
    if len(symbols) == 0:
        print(usage)
        return
    try:
        node = cast(ast.Expr, ast.parse(symbols).body[0]).value
    except SyntaxError:
        print('Could not find symbol metadata for', symbols)
        return
    if isinstance(node, ast.Tuple):
        unresolved_symbols = node.elts
    else:
        unresolved_symbols = [node]
    for unresolved in unresolved_symbols:
        dsyms = resolve_rval_symbols(unresolved, should_update_usage_info=False)
        if len(dsyms) == 0:
            print('Could not find symbol metadata for', astunparse.unparse(unresolved))
        for dsym in dsyms:
            parents = {par for par in dsym.parents if not par.is_anonymous}
            if dsym.required_cell_num > 0:
                dsym_extra_info = 'defined {}; required {}'.format(dsym.defined_cell_num, dsym.required_cell_num)
            else:
                dsym_extra_info = 'defined in cell {}'.format(dsym.defined_cell_num)
            print('Symbol {} ({}) is dependent on {}'.format(
                dsym.full_namespace_path,
                dsym_extra_info,
                parents or 'nothing'
            ))


def show_stale(line_: str):
    usage = 'Usage: %safety show_stale [global|all]'
    line = line_.split()
    if len(line) == 0 or line[0] == 'global':
        dsym_sets: Iterable[Iterable[DataSymbol]] = [nbs().global_scope.all_data_symbols_this_indentation()]
    elif line[0] == 'all':
        dsym_sets = nbs().aliases.values()
    else:
        print(usage)
        return
    stale_set = set()
    for dsym_set in dsym_sets:
        for data_sym in dsym_set:
            if data_sym.is_stale and not data_sym.is_anonymous:
                stale_set.add(data_sym)
    if not stale_set:
        print('No symbol has stale dependencies for now!')
    else:
        print('Symbol(s) with stale dependencies are:', stale_set)


def trace_messages(line_: str):
    line = line_.split()
    usage = 'Usage: %safety trace_messages [enable|disable]'
    if len(line) != 1:
        print(usage)
        return
    setting = line[0].lower()
    if setting == 'on' or setting.startswith('enable'):
        nbs().trace_messages_enabled = True
    elif setting == 'off' or setting.startswith('disable'):
        nbs().trace_messages_enabled = False
    else:
        print(usage)


def set_highlights(cmd: str, rest: str):
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
            print(usage)


def make_slice(line: str):
    usage = 'Usage: %safety slice <cell_num>'
    try:
        cell_num = int(line)
    except:
        print(usage)
        return
    deps = list(nbs().get_cell_dependencies(cell_num).items())
    deps.sort()
    print('\n\n'.join(f'# Cell {cell_num}\n' + content for cell_num, content in deps))


def _find_symbols(syms):
    results = []
    for sym in syms:
        result = nbs().global_scope.lookup_data_symbol_by_name(sym)
        if result is None:
            print('Could not find symbol metadata for', sym)
        results.append(result)
    return results


def remove_dep(line_: str):
    usage = 'Usage: %safety remove_dependency <parent_name> <child_name>'
    line = line_.split()
    if len(line) != 2:
        print(usage)
        return
    results = _find_symbols(line)
    if len(results) != len(line):
        return
    parent_data_sym, child_data_sym = results
    if parent_data_sym not in child_data_sym.parents:
        print('Two symbols do not have a dependency relation')
        return
    for children in parent_data_sym.children_by_cell_position.values():
        children.remove(child_data_sym)
    child_data_sym.parents.remove(parent_data_sym)


def add_dep(line_: str):
    usage = 'Usage: %safety add_dependency <parent_name> <child_name>'
    line = line_.split()
    if len(line) != 2:
        print(usage)
        return
    results = _find_symbols(line)
    if len(results) != len(line):
        return
    parent_data_sym, child_data_sym = results
    if parent_data_sym in child_data_sym.parents:
        print('Two symbols already have a dependency relation')
        return
    parent_data_sym.children_by_cell_position[-1].add(child_data_sym)
    child_data_sym.parents.add(parent_data_sym)


def turn_off_warnings_for(line_: str):
    usage = 'Usage: %safety turn_off_warnings_for <variable_name> <variable_name2> ...'
    line = line_.split()
    if len(line) <= 1:
        print(usage)
        return
    for data_sym_name in line:
        data_sym = nbs().global_scope.lookup_data_symbol_by_name(data_sym_name)
        if data_sym:
            data_sym.disable_warnings = True
            print('Warnings are turned off for', data_sym_name)
        else:
            print('Could not find symbol metadata for', data_sym_name)


def turn_on_warnings_for(line_: str):
    usage = 'Usage: %safety turn_on_warnings_for <variable_name> <variable_name2> ...'
    line = line_.split()
    if len(line) == 0:
        print(usage)
        return
    for data_sym_name in line:
        data_sym = nbs().global_scope.lookup_data_symbol_by_name(data_sym_name)
        if data_sym:
            data_sym.disable_warnings = False
            print('Warnings are turned on for', data_sym_name)
        else:
            print('Could not find symbol metadata for', data_sym_name)
