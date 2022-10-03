# -*- coding: utf-8 -*-
import logging
import os
import sys
from test.utils import make_flow_fixture

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

    fakelib_method = (
        fakelib.OnlyPresentSoThatHandlersCanBeRegistered.method_for_method_stub_presence
    )
    fakelib_function = fakelib.function_for_function_stub_presence
    sys.modules.pop(fakelib.__name__)

    assert (
        fakelib.__name__ not in REGISTERED_CLASS_SPECS
        and fakelib.__name__ not in REGISTERED_FUNCTION_SPECS
    )
    assert fakelib_method not in REGISTERED_HANDLER_BY_FUNCTION
    assert fakelib_function not in REGISTERED_HANDLER_BY_FUNCTION

    register_annotations_directory(os.path.dirname(__file__))
    assert (
        fakelib.__name__ in REGISTERED_CLASS_SPECS
        and fakelib.__name__ in REGISTERED_FUNCTION_SPECS
    )
    assert fakelib_method not in REGISTERED_HANDLER_BY_FUNCTION
    assert fakelib_function not in REGISTERED_HANDLER_BY_FUNCTION

    sys.modules[fakelib.__name__] = fakelib
    compile_and_register_handlers_for_module(fakelib)
    assert fakelib_method in REGISTERED_HANDLER_BY_FUNCTION
    assert fakelib_function in REGISTERED_HANDLER_BY_FUNCTION
