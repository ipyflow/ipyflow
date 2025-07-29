# -*- coding: utf-8 -*-
import logging
from test.utils import make_flow_fixture

from ipyflow.models import cells
from ipyflow.slicing.context import SlicingContext, dynamic_slicing_context

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _flow_fixture, run_cell_ = make_flow_fixture(trace_messages_enabled=True)
_flow_fixture, run_cell = make_flow_fixture()


def test_simple():
    with dynamic_slicing_context():
        run_cell("x = 0")
        run_cell("y = x + 1")
        assert cells().from_id(2).raw_parents.keys() == {1}
        assert cells().from_id(1).raw_children.keys() == {2}, (
            "got %s" % cells().from_id(1).raw_dynamic_children
        )
        run_cell("z = x + y + 2")
        assert cells().from_id(3).raw_parents.keys() == {1, 2}
        assert cells().from_id(1).raw_children.keys() == {2, 3}
        assert cells().from_id(2).raw_children.keys() == {3}
        run_cell("x = 42")
        assert cells().from_id(3).raw_parents.keys() == {1, 2}
        assert cells().from_id(1).raw_children.keys() == {2, 3}
        assert cells().from_id(2).raw_children.keys() == {3}
        run_cell("y = x + 1")
        assert cells().from_id(3).raw_parents.keys() == {1, 2}
        assert cells().from_id(1).raw_children.keys() == {2, 3}
        assert cells().from_id(2).raw_children.keys() == {3}
        assert cells().from_id(5).raw_parents.keys() == {4}


def test_nested_list_literal_mutation_induces_edge_with_mutation_virtual_symbol():
    with dynamic_slicing_context():
        run_cell("lst = [[]]")
        run_cell("lst[0].append(0)")
        assert any(
            sym.is_mutation_virtual_symbol for sym in cells().from_id(2).raw_parents[1]
        )


def test_overwrite_class_attribute_induces_mutation():
    with dynamic_slicing_context():
        run_cell("class Foo:\n    x = 42")
        run_cell("foo = Foo()")
        run_cell("foo.x = 43")
        run_cell("foo")
        assert any(
            sym.is_mutation_virtual_symbol for sym in cells().from_id(4).raw_parents[3]
        )


def test_class_and_instance_reference():
    run_cell("class Foo:\n    x = 42")
    run_cell("foo = Foo()")
    run_cell("foo.x")
    syms = set()
    for _ in SlicingContext.iter_slicing_contexts():
        syms |= cells().from_id(3).raw_parents.get(1, set())
        syms |= cells().from_id(3).raw_parents.get(2, set())
    assert {sym.readable_name for sym in syms} == {"Foo.x", "foo.x", "foo"}
