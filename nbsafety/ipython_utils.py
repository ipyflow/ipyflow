# -*- coding: utf-8 -*-
from __future__ import annotations
from contextlib import contextmanager
from typing import TYPE_CHECKING

from IPython import get_ipython

if TYPE_CHECKING:
    from typing import Optional


def _ipython():
    return get_ipython()


class _IpythonState(object):
    def __init__(self):
        self.cell_counter: Optional[int] = None

    @contextmanager
    def save_number_of_currently_executing_cell(self):
        self.cell_counter = _ipython().execution_count
        yield
        self.cell_counter = None


_IPY = _IpythonState()


def save_number_of_currently_executing_cell():
    return _IPY.save_number_of_currently_executing_cell()


def cell_counter() -> int:
    if _IPY.cell_counter is None:
        raise ValueError('should be inside context manager here')
    return _IPY.cell_counter


def run_cell(cell):
    return _ipython().run_cell(cell, store_history=True)
