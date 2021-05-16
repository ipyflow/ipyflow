# -*- coding: future_annotations -*-
import ast
from contextlib import contextmanager
from typing import TYPE_CHECKING

from IPython import get_ipython

if TYPE_CHECKING:
    from typing import Callable, List, Optional


def _ipython():
    return get_ipython()


class _IpythonState:
    def __init__(self):
        self.cell_counter: Optional[int] = None

    @contextmanager
    def save_number_of_currently_executing_cell(self):
        self.cell_counter = _ipython().execution_count
        yield
        self.cell_counter = None

    @contextmanager
    def ast_transformer_context(self, transformers: List[ast.NodeTransformer]):
        old = _ipython().ast_transformers
        _ipython().ast_transformers = old + transformers
        yield
        _ipython().ast_transformers = old

    @contextmanager
    def input_transformer_context(self, transformers: List[Callable[[List[str]], List[str]]]):
        old = _ipython().input_transformers_post
        _ipython().input_transformers_post = old + transformers
        yield
        _ipython().input_transformers_post = old


_IPY = _IpythonState()


def save_number_of_currently_executing_cell():
    return _IPY.save_number_of_currently_executing_cell()


def ast_transformer_context(transformers):
    return _IPY.ast_transformer_context(transformers)


def cell_counter() -> int:
    if _IPY.cell_counter is None:
        raise ValueError('should be inside context manager here')
    return _IPY.cell_counter


def run_cell(cell, **kwargs):
    return _ipython().run_cell(
        cell, store_history=kwargs.pop('store_history', True), silent=kwargs.pop('silent', False)
    )


class CellNotRunYetError(ValueError):
    pass
