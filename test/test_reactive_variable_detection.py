# -*- coding: future_annotations -*-
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import List


REACTIVE_VAR_REGEX = re.compile("".join(
    r"^(?:"
    r"   (?:"
    r"      (?!')"
    r"      (?!{q})"
    r"      (?!''')"
    r"      (?!{tq})"
    r"      {any}"
    r"   ) "
    r"   |  {q}[^{q}]*{q}"
    r"   |  '[^']*'"
    r"   |  '''(?:(?!'''){any})*'''"
    r"   |  {tq}(?:(?!{tq}){any})*{tq}"
    r" )*?"
    r" (\$(?:(?!\d)\w)\w*"
    r" )".format(
        q='"',
        tq='"""',
        any=r"[\S\s]",
    ).split())
)


def extract_reactive_vars(s: str) -> List[str]:
    reactive_vars = []
    while True:
        m = REACTIVE_VAR_REGEX.match(s)
        if m is None:
            break
        reactive_vars.append(m.group(1))
        s = s[m.span()[1]:]
    return reactive_vars


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
    assert extract_reactive_vars("$foo bar $baz42") == ["$foo", "$baz42"]
    assert extract_reactive_vars("$foo $42bar $_baz42") == ["$foo", "$_baz42"]
