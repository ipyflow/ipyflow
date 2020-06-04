from nbsafety.analysis.assignment_edges import get_assignment_lval_and_rval_symbol_refs


def test_simple_implicit_tuple_assignment():
    edges = list(get_assignment_lval_and_rval_symbol_refs('a, b = c, d'))
    assert edges == [('a', 'c'), ('b', 'd')]


def test_unpack_to_one_target_assignment():
    edges = list(get_assignment_lval_and_rval_symbol_refs('a, b = c'))
    assert edges == [('a', 'c'), ('b', 'c')]


def test_simple_explicit_tuple_assignment():
    edges = list(get_assignment_lval_and_rval_symbol_refs('(a, b) = (c, d)'))
    assert edges == [('a', 'c'), ('b', 'd')]


def test_simple_explicit_list_assignment():
    edges = list(get_assignment_lval_and_rval_symbol_refs('[a, b] = [c, d]'))
    assert edges == [('a', 'c'), ('b', 'd')]


def test_multiple_tuple_assignment():
    edges = list(get_assignment_lval_and_rval_symbol_refs('a, b = c = d, e'))
    assert edges == [('a', 'd'), ('b', 'e'), ('c', 'd'), ('c', 'e')]


def test_tuple_assignment_with_dict():
    edges = list(get_assignment_lval_and_rval_symbol_refs('a, b = (d, e), {f: 5, g: h}'))
    assert edges == [('a', 'd'), ('a', 'e'), ('b', 'f'), ('b', 'g'), ('b', None), ('b', 'h')]


def test_assignment_with_constants():
    edges = list(get_assignment_lval_and_rval_symbol_refs('a, b = x + 1, y + 2'))
    assert edges == [('a', 'x'), ('a', None), ('b', 'y'), ('b', None)]


def test_nested_assignment():
    edges = list(get_assignment_lval_and_rval_symbol_refs('a, (b, [c, d]) = (d, e), [(f, g), h]'))
    assert edges == [
        ('a', 'd'),
        ('a', 'e'),
        ('b', 'f'),
        ('b', 'g'),
        ('c', 'h'),
        ('d', 'h'),
    ]
