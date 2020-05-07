# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

import networkx as nx

if TYPE_CHECKING:
    from typing import List
    from .safety import DependencySafety


USAGE = """Options:

show_graph: 
    - This will print out the dependency graph of global variables. Stale nodes are labeled red.
      Notice that user might need to call this twice to have it to work.

show_dependency <variable_name> <variable_name2> ...: 
    - This will print out the dependencies for given global variables.
      Multiple variables should be separated with spaces.

show_stale: 
    - This will print out all the global variables that are stale. 

set_disable_level <integer>:
    - level 0: Warning,    Stop Code,   Record new dependencies, (Full functionality)
    - level 1: Warning,    Run code,    Record new dependencies, (Don't stop at the warning)
    - level 2: No Warning, Run code,    Record new dependencies, (Don't show warnings)
    - level 3: No warning, Run cocde,   No new dependencies,     (Original Kernel)

remove_dependency <parent_name> <child_name>:
    - This will remove the dependency between parent variable and the child variable.

add_dependency <parent_name> <child_name>:
    - This will add the dependency between parent variable and the child variable.

turn_off_warnings_for  <variable_name> <variable_name2> ...: 
    - This will turn off the warnings for given global variables. These variables will not be
      considered as stale anymore. Multiple variables should be separated with spaces.


turn_off_warnings_for  <variable_name> <variable_name2> ...: 
    - This will turn the warnings back on for given global variables. These variables could have
      stale dependencies now. Multiple variables should be separated with spaces."""


def show_graph(safety: 'DependencySafety'):
    graph = nx.DiGraph()
    for name in safety.global_scope.data_cell_by_name:
        graph.add_node(name)
    for node in safety.global_scope.data_cell_by_name.values():
        name = node.name
        for child_node in node.children:
            graph.add_edge(name, child_node.name)
    nx.draw_networkx(
        graph,
        node_color=["#ff0000" if safety.global_scope.lookup_data_cell_by_name(name).is_stale() else "#cccccc" for name in graph.nodes()],
        arrowstyle='->',
        arrowsize=30,
        node_size=1000,
        pos=nx.drawing.layout.planar_layout(graph)
    )


def show_deps(safety: 'DependencySafety', line: 'List[str]'):
    if len(line) == 1:
        print("Usage: %safety show_dependency <variable_name> <variable_name2> ...")
        return
    for data_cell_name in line[1:]:
        data_cell = safety.global_scope.lookup_data_cell_by_name(data_cell_name)
        if data_cell:
            print("DataCell {} is dependent on {}".format(data_cell_name, [str(n) for n in data_cell.parents] if data_cell.parents else "Nothing"))
        else:
            print("Cannot find DataCell", data_cell_name)


def show_stale(safety: 'DependencySafety'):
    stale_set = set()
    for data_cell in safety.global_scope.data_cell_by_name.values():
        if data_cell.is_stale():
            stale_set.add(data_cell)
    if not stale_set:
        print("No DataCell has stale dependency for now!")
    elif len(stale_set) == 1:
        print("The only DataCell with stale dependencies is:", str(stale_set.pop()))
    else:
        print("DataCells with stale dependencies are:", [str(n) for n in stale_set])


def set_disable_level(safety: 'DependencySafety', line: 'List[str]'):
    if len(line) != 2 or line[1] not in ['0', '1', '2', '3']:
        print("""Usage: %safety set_disable_level <integer>
-level 0: Warning,    Stop Code,   Record new dependencies, (Full functionality)
-level 1: Warning,    Run code,    Record new dependencies, (Don't stop at the warning)
-level 2: No Warning, Run code,    Record new dependencies, (Don't show warnings)
-level 3: No warning, Run cocde,   No new dependencies,     (Original Kernel)""")
        return
    safety._disable_level = int(line[1])


def configure_trace_messages(safety: 'DependencySafety', line: 'List[str]'):
    if len(line) != 2:
        print("Usage: %safety trace_messages [enabled|disabled] ...")
        return
    safety.trace_messages_enabled = (line[1].lower().startswith("enable"))


def remove_dep(safety: 'DependencySafety', line: 'List[str]'):
    if len(line) != 3:
        print("Usage: %safety remove_dependency <parent_name> <child_name>")
        return
    parent_data_cell = safety.global_scope.lookup_data_cell_by_name(line[1])
    if not parent_data_cell:
        print("Cannot find DataCell", line[1])
        return
    child_data_cell = safety.global_scope.lookup_data_cell_by_name(line[2])
    if not child_data_cell:
        print("Cannot find DataCell", line[2])
        return
    if child_data_cell not in parent_data_cell.children or parent_data_cell not in child_data_cell.parents:
        print("Two DataCells do not have a dependency relation")
        return
    parent_data_cell.children.remove(child_data_cell)
    child_data_cell.parents.remove(parent_data_cell)


def add_dep(safety: 'DependencySafety', line: 'List[str]'):
    if len(line) != 3:
        print("Usage: %safety add_dependency <parent_name> <child_name>")
        return
    parent_data_cell = safety.global_scope.lookup_data_cell_by_name(line[1])
    if not parent_data_cell:
        print("Cannot find DataCell", line[1])
        return
    child_data_cell = safety.global_scope.lookup_data_cell_by_name(line[2])
    if not child_data_cell:
        print("Cannot find DataCell", line[2])
        return
    if child_data_cell in parent_data_cell.children and parent_data_cell in child_data_cell.parents:
        print("Two DataCells already have a dependency relation")
        return
    parent_data_cell.children.add(child_data_cell)
    child_data_cell.parents.add(parent_data_cell)


def turn_off_warnings_for(safety: 'DependencySafety', line: 'List[str]'):
    if len(line) <= 1:
        print("Usage: %safety turn_off_warnings_for <variable_name> <variable_name2> ...")
        return
    for data_cell_name in line[1:]:
        data_cell = safety.global_scope.lookup_data_cell_by_name(data_cell_name)
        if data_cell:
            data_cell.no_warning = True
            print("Warnings are turned off for", data_cell_name)
        else:
            print("Cannot find DataCell", data_cell_name)


def turn_on_warnings_for(safety: 'DependencySafety', line: 'List[str]'):
    if len(line) <= 1:
        print("Usage: %safety turn_on_warnings_for <variable_name> <variable_name2> ...")
        return
    for data_cell_name in line[1:]:
        data_cell = safety.global_scope.lookup_data_cell_by_name(data_cell_name)
        if data_cell:
            data_cell.no_warning = False
            print("Warnings are turned on for", data_cell_name)
        else:
            print("Cannot find DataCell", data_cell_name)
