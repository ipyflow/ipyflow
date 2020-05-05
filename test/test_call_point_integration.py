# -*- coding: utf-8 -*-
from __future__ import annotations
import logging

from .utils import skipif_known_failing, make_safety_fixture

logging.basicConfig(level=logging.ERROR)


# Reset dependency graph before each test
_safety_fixture, _safety_state, run_cell = make_safety_fixture(save_prev_trace_state_for_tests=True)


@skipif_known_failing
def test_basic():
    pass
