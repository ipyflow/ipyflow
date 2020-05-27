# -*- coding: utf-8 -*-

from nbsafety.analysis.stmt_edges import get_statement_lval_and_rval_symbol_refs
from nbsafety.analysis.lineno_stmt_map import compute_lineno_to_stmt_mapping


def test_classes():
    code = """
class Foo(object):
    pass
    
class Bar(Foo):
    pass
    
class Baz(Foo, Bar):
    pass
""".strip()
    mapping = compute_lineno_to_stmt_mapping(code)
    lvals, rvals, _ = get_statement_lval_and_rval_symbol_refs(mapping[1])
    assert lvals == {'Foo'}
    assert rvals == {'object'}
    lvals, rvals, _ = get_statement_lval_and_rval_symbol_refs(mapping[4])
    assert lvals == {'Bar'}
    assert rvals == {'Foo'}
    lvals, rvals, _ = get_statement_lval_and_rval_symbol_refs(mapping[7])
    assert lvals == {'Baz'}
    assert rvals == {'Foo', 'Bar'}


def test_for_loop():
    code = """
for i in range(10):
    a = i
    b = a + i
    lst = [a, b]
""".strip()
    mapping = compute_lineno_to_stmt_mapping(code)
    lvals, rvals, _ = get_statement_lval_and_rval_symbol_refs(mapping[1])
    assert lvals == {'i'}
    assert rvals == {'range'}
    lvals, rvals, _ = get_statement_lval_and_rval_symbol_refs(mapping[2])
    assert lvals == {'a'}
    assert rvals == {'i'}
    lvals, rvals, _ = get_statement_lval_and_rval_symbol_refs(mapping[3])
    assert lvals == {'b'}
    assert rvals == {'a', 'i'}
    lvals, rvals, _ = get_statement_lval_and_rval_symbol_refs(mapping[4])
    assert lvals == {'lst'}
    assert rvals == {'a', 'b'}


def test_context_manager():
    code = """
fname = 'file.txt'
with open(fname) as f:
    contents = f.read()
""".strip()
    mapping = compute_lineno_to_stmt_mapping(code)
    lvals, rvals, _ = get_statement_lval_and_rval_symbol_refs(mapping[2])
    assert lvals == {'f'}
    assert rvals == {'fname', 'open'}
    lvals, rvals, _ = get_statement_lval_and_rval_symbol_refs(mapping[3])
    assert lvals == {'contents'}
    # attributes should be skipped
    assert rvals == set()
