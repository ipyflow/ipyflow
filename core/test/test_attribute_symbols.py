# -*- coding: utf-8 -*-
import ast

from ipyflow.analysis.symbol_ref import Atom, SymbolRef

from .utils import make_flow_fixture

_flow_fixture, _ = make_flow_fixture()


def at(sym: str, **kwargs) -> Atom:
    return Atom(sym, **kwargs)


def cat(sym: str, **kwargs) -> Atom:
    return at(sym, is_callpoint=True, **kwargs)


def test_unit_chain():
    node = ast.parse("sym").body[0].value
    symchain = SymbolRef(node)
    assert symchain.chain == (at("sym"),)
    node = ast.parse("sym()").body[0].value
    symchain = SymbolRef(node)
    assert symchain.chain == (cat("sym"),)


def test_basic():
    node = ast.parse("a.b.c.d.e.f").body[0].value
    symchain = SymbolRef(node)
    assert symchain.chain == tuple(at(s) for s in ("a", "b", "c", "d", "e", "f"))


def test_calls_none_at_endpoints():
    node = ast.parse("a.b.c().d.e().f").body[0].value
    symchain = SymbolRef(node)
    assert symchain.chain == (at("a"), at("b"), cat("c"), at("d"), cat("e"), at("f"))


def test_calls_at_endpoints():
    node = ast.parse("a().b.c().d.e.f()").body[0].value
    symchain = SymbolRef(node)
    assert symchain.chain == (cat("a"), at("b"), cat("c"), at("d"), at("e"), cat("f"))


def test_hash():
    symchain_set = set()
    symchain_set.add(SymbolRef("f.read()"))
    symchain_set.add(SymbolRef("f.read"))
    symchain_set.add("f")
    symchain_set.add(("f", "read"))
    assert len(symchain_set) == 4
