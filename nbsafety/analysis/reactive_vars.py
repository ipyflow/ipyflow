# -*- coding: future_annotations -*-
import re
from typing import TYPE_CHECKING
from nbsafety.tracing.safety_ast_rewriter import SafetyAstRewriter

if TYPE_CHECKING:
    from typing import Callable, List, Tuple


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


def get_reactive_vars_and_positions(s: str) -> Tuple[str, List[int]]:
    portions = []
    positions = []
    while True:
        m = REACTIVE_VAR_REGEX.match(s)
        if m is None:
            portions.append(s)
            break
        start, end = m.span(1)
        positions.append(start)
        portions.append(s[:start])
        portions.append(s[start + 1:end])
        s = s[end:]
    return "".join(portions), positions


def replace_reactive_vars(s: str) -> str:
    return get_reactive_vars_and_positions(s)[0]


def replace_reactive_vars_lines(lines: List[str]) -> List[str]:
    return [replace_reactive_vars(line) for line in lines]


def make_tracking_reactive_variable_replacer(rewriter: SafetyAstRewriter) -> Callable[[List[str]], List[str]]:
    def _input_transformer(lines: List[str]) -> List[str]:
        transformed_lines = []
        for idx, line in enumerate(lines):
            line, positions = get_reactive_vars_and_positions(line)
            transformed_lines.append(line)
            for pos in positions:
                rewriter.register_reactive_var_position(idx + 1, pos)
        return transformed_lines
    return _input_transformer
