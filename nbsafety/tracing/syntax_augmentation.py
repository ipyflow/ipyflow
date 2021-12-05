# -*- coding: future_annotations -*-
import re
import sys
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from typing import Any, Callable, List, Tuple, Union
    from nbsafety.tracing.ast_rewriter import AstRewriter
    CodeType = Union[str, List[str]]
    if sys.version_info >= (3, 8):
        Pattern = re.Pattern
    else:
        Pattern = Any


class AugmentationType(Enum):
    prefix = 'prefix'
    suffix = 'suffix'
    dot = 'dot'
    binop = 'binop'


class AugmentationSpec(NamedTuple):
    aug_type: AugmentationType
    token: str
    replacement: str

    @property
    def escaped_token(self):
        return re.escape(self.token)


AUGMENTED_SYNTAX_REGEX_TEMPLATE = "".join(
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
    r" ({{token}})".format(
        q='"',                                 # quote
        tq='"""',                              # triple quote
        any=r"[\S\s]",                         # match anything (more general than '.') -- space or non-space
    ).split()
)


def replace_tokens_and_get_augmented_positions(s: str, spec: AugmentationSpec, regex: Pattern) -> Tuple[str, List[int]]:
    portions = []
    positions = []
    pos_offset = 0
    while True:
        m = regex.match(s)
        if m is None:
            portions.append(s)
            break
        start, _ = m.span(1)
        positions.append(start + pos_offset)
        portions.append(s[:start])
        portions.append(spec.replacement)
        s = s[start + len(spec.token):]
        pos_offset += start + len(spec.replacement)
    return "".join(portions), positions


def make_syntax_augmenter(rewriter: AstRewriter, aug_spec: AugmentationSpec) -> Callable[[CodeType], CodeType]:
    regex = re.compile(AUGMENTED_SYNTAX_REGEX_TEMPLATE.format(token=aug_spec.escaped_token))

    def _input_transformer(lines: CodeType) -> CodeType:
        if isinstance(lines, list):
            code_lines: List[str] = lines
        else:
            code_lines = lines.split('\n')
        transformed_lines = []
        for idx, line in enumerate(code_lines):
            line, positions = replace_tokens_and_get_augmented_positions(line, aug_spec, regex)
            transformed_lines.append(line)
            for pos in positions:
                rewriter.register_augmented_position(aug_spec, idx + 1, pos)
        if isinstance(lines, list):
            return transformed_lines
        else:
            return '\n'.join(transformed_lines)
    return _input_transformer
