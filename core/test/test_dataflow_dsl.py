# -*- coding: utf-8 -*-
import logging
import os
import sys
from test.utils import lookup_symbol_by_name, make_flow_fixture, skipif_known_failing

from ipyflow.annotations import register_annotations_directory
from ipyflow.annotations.compiler import (
    REGISTERED_CLASS_SPECS,
    REGISTERED_FUNCTION_SPECS,
    compile_and_register_handlers_for_module,
)
from ipyflow.tracing.external_calls.base_handlers import REGISTERED_HANDLER_BY_FUNCTION

logging.basicConfig(level=logging.ERROR)

# Reset things before each test
# _flow_fixture, run_cell_ = make_flow_fixture(trace_messages_enabled=True)
_flow_fixture, run_cell_ = make_flow_fixture()


def run_cell(cell, **kwargs):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell, **kwargs)


def test_annotation_registration():
    import fakelib

    fakelib_class = fakelib.OnlyPresentSoThatHandlersCanBeRegistered
    fakelib_method = fakelib_class.method_for_method_stub_presence
    fakelib_method_a = fakelib_class.method_a  # type: ignore
    fakelib_method_b = fakelib_class.method_b  # type: ignore
    fakelib_function = fakelib.function_for_function_stub_presence
    sys.modules.pop(fakelib.__name__)

    non_fakelib_module_name = "non_fakelib_module"

    for module_name in [fakelib.__name__, non_fakelib_module_name]:
        assert module_name not in REGISTERED_FUNCTION_SPECS
        assert module_name not in REGISTERED_CLASS_SPECS
    for fun in [fakelib_method, fakelib_method_a, fakelib_method_b, fakelib_function]:
        assert fun not in REGISTERED_HANDLER_BY_FUNCTION

    register_annotations_directory(os.path.dirname(__file__))
    for module_name in [fakelib.__name__, non_fakelib_module_name]:
        assert module_name in REGISTERED_FUNCTION_SPECS
        assert module_name in REGISTERED_CLASS_SPECS
    for fun in [fakelib_method, fakelib_method_a, fakelib_method_b, fakelib_function]:
        assert fun not in REGISTERED_HANDLER_BY_FUNCTION

    sys.modules[fakelib.__name__] = fakelib
    compile_and_register_handlers_for_module(fakelib)
    for fun in [fakelib_method, fakelib_method_a, fakelib_method_b, fakelib_function]:
        assert fun in REGISTERED_HANDLER_BY_FUNCTION, "%s not in there" % fun


def test_mutation_by_kwarg():
    run_cell("lst = []")
    lst_sym = lookup_symbol_by_name("lst")
    ts0 = lst_sym.timestamp
    run_cell(
        "from fakelib import fun_for_testing_kwarg; fun_for_testing_kwarg(None, lst)"
    )
    ts1 = lst_sym.timestamp
    assert ts1 > ts0
    run_cell("fun_for_testing_kwarg(bar=lst, foo=None)")
    ts2 = lst_sym.timestamp
    assert ts2 > ts1
