# -*- coding: utf-8 -*-
import logging
import os
import sys
from test.utils import (
    clear_registered_annotations,
    lookup_symbol_by_name,
    make_flow_fixture,
    skipif_known_failing,
)

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
    with clear_registered_annotations():
        _test_annotation_registration()


def _test_annotation_registration():
    import fakelib

    fakelib_class = fakelib.OnlyPresentSoThatHandlersCanBeRegistered
    fakelib_method = fakelib_class.method_for_method_stub_presence
    fakelib_method_a = fakelib_class.method_a  # type: ignore
    fakelib_method_b = fakelib_class.method_b  # type: ignore
    fakelib_function = fakelib.function_for_function_stub_presence
    sys.modules.pop(fakelib.__name__)

    if sys.version_info >= (3, 8):
        fakelib_posonly_function = fakelib.fun_for_testing_posonlyarg
    else:
        fakelib_posonly_function = None

    non_fakelib_module_name = "non_fakelib_module"

    for module_name in [fakelib.__name__, non_fakelib_module_name]:
        assert module_name not in REGISTERED_FUNCTION_SPECS
        assert module_name not in REGISTERED_CLASS_SPECS
    for fun in [
        fakelib_method,
        fakelib_method_a,
        fakelib_method_b,
        fakelib_function,
        fakelib_posonly_function,
    ]:
        if fun is None:
            continue
        assert fun not in REGISTERED_HANDLER_BY_FUNCTION

    register_annotations_directory(os.path.dirname(__file__))
    for module_name in [fakelib.__name__, non_fakelib_module_name]:
        assert module_name in REGISTERED_FUNCTION_SPECS
        assert module_name in REGISTERED_CLASS_SPECS
    for fun in [
        fakelib_method,
        fakelib_method_a,
        fakelib_method_b,
        fakelib_function,
        fakelib_posonly_function,
    ]:
        if fun is None:
            continue
        assert fun not in REGISTERED_HANDLER_BY_FUNCTION

    sys.modules[fakelib.__name__] = fakelib
    compile_and_register_handlers_for_module(fakelib)
    for fun in [
        fakelib_method,
        fakelib_method_a,
        fakelib_method_b,
        fakelib_function,
        fakelib_posonly_function,
    ]:
        if fun is None:
            continue
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


def test_mutation_by_kwonlyarg():
    run_cell("lst = []")
    lst_sym = lookup_symbol_by_name("lst")
    ts0 = lst_sym.timestamp
    run_cell(
        "from fakelib import fun_for_testing_kwonlyarg; fun_for_testing_kwonlyarg(lst, bar=None)"
    )
    ts1 = lst_sym.timestamp
    assert ts1 == ts0
    run_cell("fun_for_testing_kwonlyarg(bar=lst, foo=None)")
    ts2 = lst_sym.timestamp
    assert ts2 > ts1
    run_cell("fun_for_testing_kwonlyarg(foo=None, bar=lst)")
    ts3 = lst_sym.timestamp
    assert ts3 > ts2


def test_mutate_multiple():
    run_cell("foo, bar, baz = [], [], []")
    foo_sym = lookup_symbol_by_name("foo")
    bar_sym = lookup_symbol_by_name("bar")
    baz_sym = lookup_symbol_by_name("baz")
    foo_ts0, bar_ts0, baz_ts0 = foo_sym.timestamp, bar_sym.timestamp, baz_sym.timestamp
    run_cell(
        "from fakelib import fun_for_testing_mutate_multiple; fun_for_testing_mutate_multiple(foo, bar, baz)"
    )
    foo_ts1, bar_ts1, baz_ts1 = foo_sym.timestamp, bar_sym.timestamp, baz_sym.timestamp
    assert foo_ts1 > foo_ts0
    assert bar_ts1 == bar_ts0
    assert baz_ts1 > baz_ts0
    run_cell("fun_for_testing_mutate_multiple(bar=foo, baz=bar, foo=baz)")
    foo_ts2, bar_ts2, baz_ts2 = foo_sym.timestamp, bar_sym.timestamp, baz_sym.timestamp
    assert foo_ts2 == foo_ts1
    assert bar_ts2 > bar_ts1
    assert baz_ts2 > baz_ts1


if sys.version_info >= (3, 8):

    def test_mutation_by_posonlyarg():
        run_cell("lst = []")
        lst_sym = lookup_symbol_by_name("lst")
        ts0 = lst_sym.timestamp
        run_cell(
            "from fakelib import fun_for_testing_posonlyarg; fun_for_testing_posonlyarg(None, lst)"
        )
        ts1 = lst_sym.timestamp
        assert ts1 == ts0
        run_cell("fun_for_testing_posonlyarg(lst, bar=None)")
        ts2 = lst_sym.timestamp
        assert ts2 > ts1
        run_cell("fun_for_testing_posonlyarg(lst, None)")
        ts3 = lst_sym.timestamp
        assert ts3 > ts2
