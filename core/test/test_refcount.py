# -*- coding: utf-8 -*-
import logging

from ipyflow.data_model.data_symbol import DataSymbol
from ipyflow.singletons import flow
from test.utils import make_flow_fixture, skipif_known_failing

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
_flow_fixture, run_cell = make_flow_fixture()


def lookup_symbol(name: str) -> DataSymbol:
    ret = flow().global_scope.lookup_data_symbol_by_name_this_indentation(name)
    assert ret is not None, "got None for %s" % name
    return ret


def test_basic():
    run_cell("x = object()")
    assert lookup_symbol("x").get_ref_count() == 1
    run_cell("y = x")
    assert lookup_symbol("x").get_ref_count() == 2
    assert lookup_symbol("y").get_ref_count() == 2
    run_cell("del x")
    assert lookup_symbol("y").get_ref_count() == 1
    run_cell("y = None")
    # None has special semantics as it can mean that the symbol was gc'd
    # Right now (28/04/2021, hash 9099347) this isn't used anywhere but
    # that may change.
    assert lookup_symbol("y").get_ref_count() == -1
