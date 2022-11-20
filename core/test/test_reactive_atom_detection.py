# -*- coding: utf-8 -*-
import sys
from typing import List, Set

from pyccolo.syntax_augmentation import (
    AugmentationSpec,
    AugmentationType,
    replace_tokens_and_get_augmented_positions,
)

from ipyflow.singletons import tracer
from ipyflow.tracing.ipyflow_tracer import reactive_spec

from .utils import make_flow_fixture

_flow_fixture, run_cell = make_flow_fixture()


def _replace_reactive_atoms(s: str) -> str:
    return replace_tokens_and_get_augmented_positions(s, reactive_spec)[0]


def _get_reactive_positions(s: str) -> List[int]:
    return [
        pos[1]
        for pos in replace_tokens_and_get_augmented_positions(s, reactive_spec)[1]
    ]


def _get_augmented_positions(s: str, spec: AugmentationSpec) -> List[int]:
    return [pos[1] for pos in replace_tokens_and_get_augmented_positions(s, spec)[1]]


def _get_all_reactive_var_names() -> Set[str]:
    return {
        tracer().ast_node_by_id[node_id].id for node_id in tracer().reactive_node_ids
    }


def test_simple():
    assert _replace_reactive_atoms("$foo $bar") == "foo bar"
    assert _replace_reactive_atoms("$foo bar $baz42") == "foo bar baz42"
    assert _replace_reactive_atoms("$foo $42bar $_baz42") == "foo 42bar _baz42"


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


if sys.version_info >= (3, 8):

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
