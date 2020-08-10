# -*- coding: utf-8 -*-
import ast
from .utils import skipif_known_failing

from nbsafety.analysis.live_refs import compute_live_dead_symbol_refs


def test_simple():
    live, dead = compute_live_dead_symbol_refs("""
x = 5
print(foo, x)""")
    assert live == {'foo', 'print'}
    assert dead == {'x'}


def test_function_body():
    fbody = ast.parse("""
def func():
    y = 42
    print(foo, bar, baz, x)
    x = 5
""").body[0].body
    live, dead = compute_live_dead_symbol_refs(fbody)
    assert live == {'foo', 'bar', 'baz', 'x', 'print'}
    assert dead == {'x', 'y'}
