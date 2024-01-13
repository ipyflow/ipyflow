# -*- coding: utf-8 -*-
import itertools
from collections import defaultdict
from typing import Dict, List, Set, Union

from ipyflow.data_model.symbol import Symbol
from ipyflow.singletons import flow


def create_dag_metadata() -> (
    Dict[int, Dict[str, Union[List[int], List[str], Dict[str, Dict[str, str]]]]]
):
    flow_ = flow()
    cell_num_to_used_imports: Dict[int, Set[Symbol]] = defaultdict(set)
    cell_num_to_inputs: Dict[int, Set[Symbol]] = defaultdict(set)
    cell_num_to_outputs: Dict[int, Set[Symbol]] = defaultdict(set)
    cell_num_to_cell_parents: Dict[int, Set[int]] = defaultdict(set)
    cell_num_to_cell_children: Dict[int, Set[int]] = defaultdict(set)

    for sym in flow_.all_symbols():
        top_level_sym = sym.get_top_level()
        if (
            top_level_sym is None
            or not top_level_sym.is_globally_accessible
            or top_level_sym.is_anonymous
            or top_level_sym.name == "_"
        ):
            # TODO: also skip lambdas
            continue
        if top_level_sym.is_module and any(
            alias.is_import and alias.name == top_level_sym.name
            for alias in top_level_sym.aliases
        ):
            # don't include module symbols (which are created implicitly by import machinery)
            # if they can be covered by explicit import statements
            continue
        for (
            used_time,
            sym_timestamp_when_used,
        ) in itertools.chain(
            sym.timestamp_by_used_time.items(), sym.timestamp_by_liveness_time.items()
        ):
            if top_level_sym.is_import:
                cell_num_to_used_imports[used_time.cell_num].add(top_level_sym)
            elif used_time.cell_num != sym_timestamp_when_used.cell_num:
                cell_num_to_cell_parents[used_time.cell_num].add(
                    sym_timestamp_when_used.cell_num
                )
                cell_num_to_cell_children[sym_timestamp_when_used.cell_num].add(
                    used_time.cell_num
                )
                cell_num_to_inputs[used_time.cell_num].add(top_level_sym)
                cell_num_to_outputs[sym_timestamp_when_used.cell_num].add(top_level_sym)
        if not top_level_sym.is_import:
            for updated_time in sym.updated_timestamps:
                # TODO: distinguished between used / unused outputs?
                cell_num_to_outputs[updated_time.cell_num].add(top_level_sym)

    cell_metadata: Dict[
        int, Dict[str, Union[List[int], List[str], Dict[str, Dict[str, str]]]]
    ] = {}
    all_relevant_cells = (
        cell_num_to_used_imports.keys()
        | cell_num_to_inputs.keys()
        | cell_num_to_outputs.keys()
        | cell_num_to_cell_parents.keys()
        | cell_num_to_cell_children.keys()
    )
    for cell_num in all_relevant_cells:
        cell_imports = [
            sym.get_import_string() for sym in cell_num_to_used_imports[cell_num]
        ]
        input_symbols = {
            str(sym): {"type": sym.get_type_annotation_string()}
            for sym in cell_num_to_inputs[cell_num]
        }
        output_symbols = {
            str(sym): {"type": sym.get_type_annotation_string()}
            for sym in cell_num_to_outputs[cell_num]
        }
        parent_cells = list(cell_num_to_cell_parents[cell_num])
        child_cells = list(cell_num_to_cell_children[cell_num])
        cell_metadata[cell_num] = {
            "cell_imports": cell_imports,
            "input_symbols": input_symbols,
            "output_symbols": output_symbols,
            "parent_cells": parent_cells,
            "child_cells": child_cells,
        }
    return cell_metadata
