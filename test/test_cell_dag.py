# -*- coding: utf-8 -*-
import logging

from nbsafety.data_model.code_cell import cells
from test.utils import make_safety_fixture

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _safety_fixture, run_cell_ = make_safety_fixture(trace_messages_enabled=True)
_safety_fixture, run_cell = make_safety_fixture()


def test_simple():
    run_cell("x = 0")
    run_cell("y = x + 1")
    assert cells().from_id(2).dynamic_parent_ids == {1}
    assert cells().from_id(1).dynamic_children_ids == {2}, (
        "got %s" % cells().from_id(1)._dynamic_children
    )
    run_cell("z = x + y + 2")
    assert cells().from_id(3).dynamic_parent_ids == {1, 2}
    assert cells().from_id(1).dynamic_children_ids == {2, 3}
    assert cells().from_id(2).dynamic_children_ids == {3}
    run_cell("x = 42")
    assert cells().from_id(3).dynamic_parent_ids == {1, 2}
    assert cells().from_id(1).dynamic_children_ids == {2, 3}
    assert cells().from_id(2).dynamic_children_ids == {3}
    run_cell("y = x + 1")
    assert cells().from_id(3).dynamic_parent_ids == {1, 2}
    assert cells().from_id(1).dynamic_children_ids == {2, 3}
    assert cells().from_id(2).dynamic_children_ids == {3}
    assert cells().from_id(5).dynamic_parent_ids == {4}
