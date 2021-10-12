# -*- coding: future_annotations -*-
from typing import TYPE_CHECKING
from nbsafety.analysis.reactive_vars import REACTIVE_VAR_REGEX, extract_reactive_vars, replace_reactive_vars
from nbsafety.singletons import nbs
from .utils import make_safety_fixture

if TYPE_CHECKING:
    from typing import Set

_safety_fixture, run_cell = make_safety_fixture(enable_reactive_variables=True)


def _get_all_reactive_var_names() -> Set[str]:
    return {
        nbs().ast_node_by_id[node_id].id
        for node_id in nbs().reactive_variable_node_ids
    }


def test_simple():
    assert REACTIVE_VAR_REGEX.match("") is None
    assert REACTIVE_VAR_REGEX.match("foo") is None
    assert REACTIVE_VAR_REGEX.match("foo bar") is None
    assert REACTIVE_VAR_REGEX.match("$foo").group(1) == "$foo"
    assert REACTIVE_VAR_REGEX.match("$foo bar").group(1) == "$foo"
    assert REACTIVE_VAR_REGEX.match("foo $bar").group(1) == "$bar"
    assert REACTIVE_VAR_REGEX.match("\nfoo $bar").group(1) == "$bar"
    assert REACTIVE_VAR_REGEX.match("\n$foo bar").group(1) == "$foo"
    assert REACTIVE_VAR_REGEX.match("\n'$foo' $bar").group(1) == "$bar"
    assert extract_reactive_vars("$foo $bar") == ["$foo", "$bar"]
    assert replace_reactive_vars("$foo $bar") == "foo bar"
    assert extract_reactive_vars("$foo bar $baz42") == ["$foo", "$baz42"]
    assert replace_reactive_vars("$foo bar $baz42") == "foo bar baz42"
    assert extract_reactive_vars("$foo $42bar $_baz42") == ["$foo", "$_baz42"]
    assert replace_reactive_vars("$foo $42bar $_baz42") == "foo $42bar _baz42"


def test_simple_names_recovered():
    run_cell('x = 0')
    run_cell('y = $x + 1')
    assert _get_all_reactive_var_names() == {'x'}
    run_cell('z = $y + 2')
    assert _get_all_reactive_var_names() == {'x', 'y'}
    run_cell('w1 = $z + 2\nw2 = $w1 + 3')
    assert _get_all_reactive_var_names() == {'x', 'y', 'z', 'w1'}
