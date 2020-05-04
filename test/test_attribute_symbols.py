# -*- coding: utf-8 -*-
from __future__ import annotations
import ast

from nbsafety.analysis.stmt_edges import get_attribute_symbols


def test_basic():
    node = ast.parse('a.b.c.d.e.f').body[0].value
    assert get_attribute_symbols(node) == ['a', 'b', 'c', 'd', 'e', 'f']


def test_calls_none_at_endpoints():
    node = ast.parse('a.b.c().d.e().f').body[0].value
    assert get_attribute_symbols(node) == ['a', 'b', 'c', 'd', 'e', 'f']


def test_calls_at_endpoints():
    node = ast.parse('a().b.c().d.e.f()').body[0].value
    assert get_attribute_symbols(node) == ['a', 'b', 'c', 'd', 'e', 'f']
