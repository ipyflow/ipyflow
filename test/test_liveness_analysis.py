# -*- coding: future_annotations -*-
import ast
import sys

from nbsafety.analysis.live_refs import compute_live_dead_symbol_refs as compute_live_dead_symbol_refs_with_stmts


def compute_live_dead_symbol_refs(code):
    live, dead = compute_live_dead_symbol_refs_with_stmts(code)
    live = {ref[0] for ref in live}
    live, dead = _remove_callpoints(live), _remove_callpoints(dead)
    return live, dead


def _remove_callpoints(symbols):
    return set(sym for sym in symbols if isinstance(sym, str))


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


def test_comprehension_with_killed_elt():
    live, dead = compute_live_dead_symbol_refs('[x for y in range(10) for x in range(11)]')
    assert live == {'range'}
    assert dead == {'x', 'y'}


def test_comprehension_with_live_elt():
    live, dead = compute_live_dead_symbol_refs('[x for y in range(10) for _ in range(11)]')
    assert live == {'x', 'range'}
    assert dead == {'y', '_'}


def test_subscript_is_live():
    live, dead = compute_live_dead_symbol_refs('foo[bar] = baz')
    assert live == {'bar', 'baz'}


if sys.version_info >= (3, 8):
    def test_walrus():
        live, dead = compute_live_dead_symbol_refs("""
if (y := (x := x + 1) + 1) > 0:
    z = y + 1
""")
        assert live == {'x'}
        assert dead == {'y', 'z'}, 'got %s' % dead
