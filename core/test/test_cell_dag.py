# -*- coding: utf-8 -*-
import logging
from test.utils import make_flow_fixture

from ipyflow.data_model.cell import cells
from ipyflow.slicing.context import dynamic_slicing_context

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _flow_fixture, run_cell_ = make_flow_fixture(trace_messages_enabled=True)
_flow_fixture, run_cell = make_flow_fixture()


def test_simple():
    with dynamic_slicing_context():
        run_cell("x = 0")
        run_cell("y = x + 1")
        assert cells().from_id(2).parents.keys() == {1}
        assert cells().from_id(1).children.keys() == {2}, (
            "got %s" % cells().from_id(1).dynamic_children
        )
        run_cell("z = x + y + 2")
        assert cells().from_id(3).parents.keys() == {1, 2}
        assert cells().from_id(1).children.keys() == {2, 3}
        assert cells().from_id(2).children.keys() == {3}
        run_cell("x = 42")
        assert cells().from_id(3).parents.keys() == {1, 2}
        assert cells().from_id(1).children.keys() == {2, 3}
        assert cells().from_id(2).children.keys() == {3}
        run_cell("y = x + 1")
        assert cells().from_id(3).parents.keys() == {1, 2}
        assert cells().from_id(1).children.keys() == {2, 3}
        assert cells().from_id(2).children.keys() == {3}
        assert cells().from_id(5).parents.keys() == {4}
