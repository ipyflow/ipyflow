# -*- coding: utf-8 -*-
from collections import defaultdict
from typing import Dict, List, Set, Union

from ipyflow.data_model.data_symbol import DataSymbol
from ipyflow.singletons import flow


def create_dag_metadata() -> Dict[
    int, Dict[str, Union[List[int], List[str], Dict[str, Dict[str, str]]]]
]:
    flow_ = flow()
    cell_num_to_used_imports: Dict[int, Set[DataSymbol]] = defaultdict(set)
    cell_num_to_dynamic_inputs: Dict[int, Set[DataSymbol]] = defaultdict(set)
    cell_num_to_dynamic_outputs: Dict[int, Set[DataSymbol]] = defaultdict(set)
    cell_num_to_dynamic_cell_parents: Dict[int, Set[int]] = defaultdict(set)
    cell_num_to_dynamic_cell_children: Dict[int, Set[int]] = defaultdict(set)

    for sym in flow_.all_data_symbols():
        top_level_sym = sym.get_top_level()
        if (
            top_level_sym is None
            or not top_level_sym.is_globally_accessible
            or top_level_sym.is_anonymous
        ):
            # TODO: also skip lambdas
            continue
        for (
            used_time,
            sym_timestamp_when_used,
        ) in sym.timestamp_by_used_time.items():
            if top_level_sym.is_import:
                cell_num_to_used_imports[used_time.cell_num].add(top_level_sym)
            elif used_time.cell_num != sym_timestamp_when_used.cell_num:
                cell_num_to_dynamic_cell_parents[used_time.cell_num].add(
                    sym_timestamp_when_used.cell_num
                )
                cell_num_to_dynamic_cell_children[sym_timestamp_when_used.cell_num].add(
                    used_time.cell_num
                )
                cell_num_to_dynamic_inputs[used_time.cell_num].add(top_level_sym)
                cell_num_to_dynamic_outputs[sym_timestamp_when_used.cell_num].add(
                    top_level_sym
                )
        if not top_level_sym.is_import:
            for updated_time in sym.updated_timestamps:
                # TODO: distinguished between used / unused outputs?
                cell_num_to_dynamic_outputs[updated_time.cell_num].add(top_level_sym)

    cell_metadata: Dict[
        int, Dict[str, Union[List[int], List[str], Dict[str, Dict[str, str]]]]
    ] = {}
    all_relevant_cells = (
        cell_num_to_used_imports.keys()
        | cell_num_to_dynamic_inputs.keys()
        | cell_num_to_dynamic_outputs.keys()
        | cell_num_to_dynamic_cell_parents.keys()
        | cell_num_to_dynamic_cell_children.keys()
    )
    for cell_num in all_relevant_cells:
        cell_imports = [
            dsym.get_import_string() for dsym in cell_num_to_used_imports[cell_num]
        ]
        input_symbols = {
            str(dsym): {"type": dsym.get_type_annotation_string()}
            for dsym in cell_num_to_dynamic_inputs[cell_num]
        }
        output_symbols = {
            str(dsym): {"type": dsym.get_type_annotation_string()}
            for dsym in cell_num_to_dynamic_outputs[cell_num]
        }
        parent_cells = list(cell_num_to_dynamic_cell_parents[cell_num])
        child_cells = list(cell_num_to_dynamic_cell_children[cell_num])
        cell_metadata[cell_num] = {
            "cell_imports": cell_imports,
            "input_symbols": input_symbols,
            "output_symbols": output_symbols,
            "parent_cells": parent_cells,
            "child_cells": child_cells,
        }
    return cell_metadata
