# -*- coding: utf-8 -*-
from typing import Optional

from ipyflow.data_model.code_cell import cells


def stdout(cell_num: int) -> Optional[str]:
    try:
        captured = cells().from_counter(cell_num).captured_output
        return None if captured is None else str(captured.stdout)
    except KeyError:
        raise ValueError("cell with counter %d has not yet executed" % cell_num)


def stderr(cell_num: int) -> Optional[str]:
    try:
        captured = cells().from_counter(cell_num).captured_output
        return None if captured is None else str(captured.stderr)
    except KeyError:
        raise ValueError("cell with counter %d has not yet executed" % cell_num)
