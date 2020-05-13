# -*- coding: utf-8 -*-
import ast
from contextlib import contextmanager
from typing import TYPE_CHECKING

from IPython import get_ipython

if TYPE_CHECKING:
    from typing import List, Optional, Union, Type


def _ipython():
    return get_ipython()


class _IpythonState(object):
    def __init__(self):
        self.cell_counter: Optional[int] = None

    @contextmanager
    def save_number_of_currently_executing_cell(self):
        self.cell_counter = _ipython().execution_count
        yield
        self.cell_counter = None

    @contextmanager
    def ast_transformer_context(
            self, transformers: 'Union[List[Union[ast.NodeTransformer, Type]], ast.NodeTransformer, Type]'
    ):
        if not isinstance(transformers, list):
            transformers = [transformers]
        transformers = [t if isinstance(t, ast.NodeTransformer) else t() for t in transformers]
        old = _ipython().ast_transformers
        _ipython().ast_transformers = old + transformers
        yield
        _ipython().ast_transformers = old


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
