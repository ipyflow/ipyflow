# -*- coding: future_annotations -*-
import ast
import re
from typing import TYPE_CHECKING

from nbsafety.singletons import nbs

if TYPE_CHECKING:
    from typing import Dict, Generator, Optional
    from nbsafety.types import CellId


_NB_MAGIC_PATTERN = re.compile(r'(^%|^!|^cd |\?$)')


class CodeCell:
    current_cell_by_cell_id: Dict[CellId, CodeCell] = {}
    cell_by_cell_ctr: Dict[int, CodeCell] = {}

    def __init__(self, cell_id: CellId, cell_ctr: int, content: str) -> None:
        self.cell_id = cell_id
        self.cell_ctr = cell_ctr
        self.content = content

    @classmethod
    def create_and_track(cls, cell_id: CellId, cell_ctr: int, content: str) -> CodeCell:
        cell = cls(cell_id, cell_ctr, content)
        cls.cell_by_cell_ctr[cell_ctr] = cell
        cur_cell = cls.current_cell_by_cell_id.get(cell_id, None)
        cur_cell_ctr = None if cur_cell is None else cur_cell.cell_ctr
        if cur_cell_ctr is None or cell_ctr > cur_cell_ctr:
            cls.current_cell_by_cell_id[cell_id] = cell
        return cell

    @classmethod
    def clear(cls):
        cls.current_cell_by_cell_id.clear()
        cls.cell_by_cell_ctr.clear()

    @classmethod
    def all_run_cells(cls) -> Generator[CodeCell, None, None]:
        yield from cls.current_cell_by_cell_id.values()

    @classmethod
    def from_counter(cls, ctr: int) -> CodeCell:
        return cls.cell_by_cell_ctr[ctr]

    @classmethod
    def from_id(cls, cell_id: CellId) -> Optional[CodeCell]:
        return cls.current_cell_by_cell_id.get(cell_id, None)

    def sanitized_content(self):
        lines = []
        for line in self.content.strip().split('\n'):
            # TODO: figure out more robust strategy for filtering / transforming lines for the ast parser
            # we filter line magics, but for %time, we would ideally like to trace the statement being timed
            # TODO: how to do this?
            if _NB_MAGIC_PATTERN.search(line.strip()) is None:
                lines.append(line)
        return '\n'.join(lines)

    def ast(self) -> ast.Module:
        return ast.parse(self.sanitized_content())

    @property
    def is_current(self) -> bool:
        return self.current_cell_by_cell_id.get(self.cell_id, None) is self

    @classmethod
    def current_cell(cls) -> CodeCell:
        return cls.cell_by_cell_ctr[nbs().cell_counter()]
