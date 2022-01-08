# -*- coding: utf-8 -*-
import re
from typing import List, Set
from nbsafety.tracing.nbsafety_tracer import reactive_spec
from pyccolo.syntax_augmentation import (
    AugmentationType,
    AugmentationSpec,
    AUGMENTED_SYNTAX_REGEX_TEMPLATE,
    replace_tokens_and_get_augmented_positions,
)
from nbsafety.singletons import tracer
from .utils import make_safety_fixture

_safety_fixture, run_cell = make_safety_fixture(enable_reactive_modifiers=True)


REACTIVE_ATOM_REGEX = re.compile(
    AUGMENTED_SYNTAX_REGEX_TEMPLATE.format(token=reactive_spec.escaped_token)
)


def _replace_reactive_atoms(s: str) -> str:
    return replace_tokens_and_get_augmented_positions(
        s, reactive_spec, REACTIVE_ATOM_REGEX
    )[0]


def _get_reactive_positions(s: str) -> List[int]:
    return replace_tokens_and_get_augmented_positions(
        s, reactive_spec, REACTIVE_ATOM_REGEX
    )[1]


def _get_augmented_positions(s: str, spec: AugmentationSpec) -> List[int]:
    regex = re.compile(AUGMENTED_SYNTAX_REGEX_TEMPLATE.format(token=spec.escaped_token))
    return replace_tokens_and_get_augmented_positions(s, spec, regex)[1]


def _get_all_reactive_var_names() -> Set[str]:
    return {
        tracer().ast_node_by_id[node_id].id for node_id in tracer().reactive_node_ids
    }


def test_simple():
    assert REACTIVE_ATOM_REGEX.match("") is None
    assert REACTIVE_ATOM_REGEX.match("foo") is None
    assert REACTIVE_ATOM_REGEX.match("foo bar") is None
    assert _replace_reactive_atoms("$foo $bar") == "foo bar"
    assert _replace_reactive_atoms("$foo bar $baz42") == "foo bar baz42"
    assert _replace_reactive_atoms("$foo $42bar $_baz42") == "foo 42bar _baz42"


def test_simple_names_recovered():
    run_cell("x = 0")
    run_cell("y = $x + 1")
    assert _get_all_reactive_var_names() == {"x"}
    run_cell("z = $y + 2")
    assert _get_all_reactive_var_names() == {"x", "y"}
    run_cell("w1 = $z + 2\nw2 = $w1 + 3")
    assert _get_all_reactive_var_names() == {"x", "y", "z", "w1"}


def test_nested_names_recovered():
    run_cell(
        """
        def assert_nonzero(v):
            assert v != 0
        """
    )
    run_cell("x = 42")
    run_cell("$assert_nonzero($x)")
    varnames = _get_all_reactive_var_names()
    assert varnames == {"x", "assert_nonzero"}, "got %s" % varnames


def test_reactive_positions():
    assert _get_reactive_positions("foo") == []
    assert _get_reactive_positions("$foo") == [0]
    assert _get_reactive_positions("foo $bar") == [4]
    assert _get_reactive_positions("$foo $bar") == [0, 4]
    assert _get_reactive_positions("$foo $bar $baz") == [0, 4, 8]
    assert _get_reactive_positions("foo $bar $baz") == [4, 8]
    assert _get_reactive_positions("$foo bar $baz") == [0, 8]


def test_positions_with_offset_from_replacement():
    spec = AugmentationSpec(AugmentationType.prefix, "$$", "$")
    assert _get_augmented_positions("foo", spec) == []
    assert _get_augmented_positions("$$foo", spec) == [0]
    assert _get_augmented_positions("foo $$bar", spec) == [4]
    assert _get_augmented_positions("$$foo $$bar", spec) == [0, 5]
    assert _get_augmented_positions("$$foo $$bar $$baz", spec) == [0, 5, 10]
    assert _get_augmented_positions("foo $$bar $$baz", spec) == [4, 9]
    assert _get_augmented_positions("$$foo bar $$baz", spec) == [0, 9]
