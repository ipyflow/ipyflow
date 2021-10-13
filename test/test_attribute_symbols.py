# -*- coding: future_annotations -*-
import ast

from nbsafety.analysis.symbol_ref import get_attrsub_symbol_chain, Atom as at


def test_basic():
    node = ast.parse('a.b.c.d.e.f').body[0].value
    symchain = get_attrsub_symbol_chain(node)
    assert symchain.chain == tuple(at(s) for s in ('a', 'b', 'c', 'd', 'e', 'f'))


def test_calls_none_at_endpoints():
    node = ast.parse('a.b.c().d.e().f').body[0].value
    symchain = get_attrsub_symbol_chain(node)
    assert symchain.chain == (at('a'), at('b'), at('c', is_callpoint=True), at('d'), at('e', is_callpoint=True), at('f'))


def test_calls_at_endpoints():
    node = ast.parse('a().b.c().d.e.f()').body[0].value
    symchain = get_attrsub_symbol_chain(node)
    assert symchain.chain == (at('a', is_callpoint=True), at('b'), at('c', is_callpoint=True), at('d'), at('e'), at('f', is_callpoint=True))


def test_hash():
    symchain_set = set()
    symchain_set.add(get_attrsub_symbol_chain('f.read()'))
    symchain_set.add(get_attrsub_symbol_chain('f.read'))
    symchain_set.add('f')
    symchain_set.add(('f', 'read'))
    assert len(symchain_set) == 4
