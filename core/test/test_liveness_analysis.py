# -*- coding: utf-8 -*-
import ast
import sys
import textwrap
from typing import Set, Tuple, Union

from ipyflow.analysis.live_refs import (
    compute_live_dead_symbol_refs as compute_live_dead_symbol_refs_with_stmts,
)
from ipyflow.analysis.symbol_ref import SymbolRef

from .utils import make_flow_fixture

_flow_fixture, _ = make_flow_fixture()


def _simplify_symbol_refs(symbols: Set[SymbolRef]) -> Set[str]:
    simplified = set()
    for sym in symbols:
        if isinstance(sym, SymbolRef):
            sym = sym.chain[0].value
        if isinstance(sym, str):
            simplified.add(sym)
    return simplified


def compute_live_dead_symbol_refs(
    code: Union[str, ast.AST]
) -> Tuple[Set[str], Set[str]]:
    if isinstance(code, str):
        code = textwrap.dedent(code)
    live, dead = compute_live_dead_symbol_refs_with_stmts(code)
    live = {ref.ref for ref in live}
    live, dead = _simplify_symbol_refs(live), _simplify_symbol_refs(dead)
    return live, dead


def test_simple():
    live, dead = compute_live_dead_symbol_refs(
        """
        x = 5
        print(foo, x)
        """
    )
    assert live == {"foo", "print"}
    assert dead == {"x"}


def test_function_body():
    fbody = (
        ast.parse(
            textwrap.dedent(
                """
        def func():
            y = 42
            print(foo, bar, baz, x)
            x = 5
        """
            )
        )
        .body[0]
        .body
    )
    live, dead = compute_live_dead_symbol_refs(fbody)
    assert live == {"foo", "bar", "baz", "x", "print"}
    assert dead == {"x", "y"}


def test_comprehension_with_killed_elt():
    live, dead = compute_live_dead_symbol_refs(
        "[x for y in range(10) for x in range(11)]"
    )
    assert live == {"range"}
    assert dead == set()


def test_comprehension_with_live_elt():
    live, dead = compute_live_dead_symbol_refs(
        "[x for y in range(10) for _ in range(11)]"
    )
    assert live == {"x", "range"}, "got %s" % live
    assert dead == set()


def test_subscript_is_live():
    live, dead = compute_live_dead_symbol_refs("foo[bar] = baz")
    assert live == {"foo", "bar", "baz"}


def test_dict_literal():
    live, dead = compute_live_dead_symbol_refs("{'foo': bar}")
    assert live == {"bar"}


if sys.version_info >= (3, 8):

    def test_walrus():
        live, dead = compute_live_dead_symbol_refs(
            """
            if (y := (x := x + 1) + 1) > 0:
                z = y + 1
            """
        )
        assert live == {"x"}
        assert dead == {"y", "z"}, "got %s" % dead
