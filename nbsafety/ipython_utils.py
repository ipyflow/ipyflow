# -*- coding: utf-8 -*-
from __future__ import annotations
from contextlib import contextmanager
from typing import TYPE_CHECKING

from IPython import get_ipython

if TYPE_CHECKING:
    from typing import Optional

_CELL_COUNTER: Optional[int] = None


@contextmanager
def save_number_of_currently_executing_cell():
    global _CELL_COUNTER
    _CELL_COUNTER = get_ipython().execution_count
    yield
    _CELL_COUNTER = None


def cell_counter() -> int:
    if _CELL_COUNTER is None:
        return get_ipython().execution_count
    return _CELL_COUNTER
