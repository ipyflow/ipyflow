# -*- coding: utf-8 -*-
import logging
import os
from test.utils import make_flow_fixture

from ipyflow.annotations import register_annotations_directory
from ipyflow.annotations.compiler import (
    REGISTERED_CLASS_SPECS,
    REGISTERED_FUNCTION_SPECS,
)

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


def test_annotations_get_registered():
    import fakelib

    assert (
        fakelib.__name__ not in REGISTERED_CLASS_SPECS
        and fakelib.__name__ not in REGISTERED_FUNCTION_SPECS
    )
    register_annotations_directory(os.path.dirname(__file__))
    assert (
        fakelib.__name__ in REGISTERED_CLASS_SPECS
        and fakelib.__name__ in REGISTERED_FUNCTION_SPECS
    )
