# -*- coding: utf-8 -*-
from typing import Optional, Union

from ipyflow.data_model.code_cell import cells
from ipyflow.data_model.timestamp import Timestamp


def _to_cell_num(ts_or_cell_num: Union[int, Timestamp]) -> int:
    return (
        ts_or_cell_num.cell_num
        if isinstance(ts_or_cell_num, Timestamp)
        else ts_or_cell_num
    )


def stdout(ts_or_cell_num: Union[int, Timestamp]) -> Optional[str]:
    try:
        cell_num = _to_cell_num(ts_or_cell_num)
        captured = cells().from_counter(cell_num).captured_output
        return None if captured is None else str(captured.stdout)
    except KeyError:
        raise ValueError("cell with counter %d has not yet executed" % cell_num)


def stderr(ts_or_cell_num: Union[int, Timestamp]) -> Optional[str]:
    try:
        cell_num = _to_cell_num(ts_or_cell_num)
        captured = cells().from_counter(cell_num).captured_output
        return None if captured is None else str(captured.stderr)
        captured = cells().from_counter(cell_num).captured_output
        return None if captured is None else str(captured.stderr)
    except KeyError:
        raise ValueError("cell with counter %d has not yet executed" % cell_num)
