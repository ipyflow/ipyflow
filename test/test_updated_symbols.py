# -*- coding: future_annotations -*-
import logging

from nbsafety.singletons import nbs
from test.utils import make_safety_fixture

logging.basicConfig(level=logging.ERROR)

# Reset dependency graph before each test
# _safety_fixture, run_cell_ = make_safety_fixture(trace_messages_enabled=True)
_safety_fixture, run_cell_ = make_safety_fixture()


def updated_symbol_names():
    return sorted([sym.readable_name for sym in nbs().updated_symbols if not sym.is_anonymous])


def run_cell(cell):
    # print()
    # print('*******************************************')
    # print('running', cell)
    # print('*******************************************')
    # print()
    run_cell_(cell)


def test_simplest():
    run_cell('a = 0')
    assert updated_symbol_names() == ['a']
    run_cell('a += 1')
    assert updated_symbol_names() == ['a']


def test_dict_hierarchy():
    run_cell('d = {}')
    updated_sym_names = updated_symbol_names()
    assert updated_sym_names == ['d'], 'got %s' % updated_sym_names
    run_cell('d["foo"] = {}')
    assert updated_symbol_names() == sorted(['d[foo]', 'd'])
    run_cell('d["foo"]["bar"] = []')
    updated_sym_names = updated_symbol_names()
    assert updated_sym_names == sorted(['d[foo][bar]', 'd[foo]', 'd']), 'got %s' % updated_sym_names
    run_cell('d["foo"]["bar"] = 0')
    assert updated_symbol_names() == sorted(['d[foo][bar]', 'd[foo]', 'd'])
