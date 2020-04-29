# -*- coding: utf-8 -*-
from __future__ import annotations

from IPython import get_ipython


def cell_counter() -> int:
    return get_ipython().execution_count - 1
