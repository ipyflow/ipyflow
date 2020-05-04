# -*- coding: utf-8 -*-
from __future__ import annotations
import ast

from nbsafety.analysis.attr_symbols import get_attribute_symbols, CallPoint as Cp


def test_basic():
    node = ast.parse('a.b.c.d.e.f').body[0].value
    syms, cps = get_attribute_symbols(node)
    assert syms == ['a', 'b', 'c', 'd', 'e', 'f']
    assert cps == []


def test_calls_none_at_endpoints():
    node = ast.parse('a.b.c().d.e().f').body[0].value
    syms, cps = get_attribute_symbols(node)
    assert syms == ['a', 'b', Cp('c'), 'd', Cp('e'), 'f']
    assert cps == [Cp('c'), Cp('e')]


def test_calls_at_endpoints():
    node = ast.parse('a().b.c().d.e.f()').body[0].value
    syms, cps = get_attribute_symbols(node)
    assert syms == [Cp('a'), 'b', Cp('c'), 'd', 'e', Cp('f')]
    assert cps == [Cp('a'), Cp('c'), Cp('f')]
