# -*- coding: utf-8 -*-
import ast
import logging
from contextlib import contextmanager
from typing import Callable, Generator, List, Optional

from IPython.core.interactiveshell import ExecutionResult
from traitlets import MetaHasTraits

from ipyflow.singletons import shell

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class _IpythonState:
    def __init__(self) -> None:
        self.cell_counter: Optional[int] = None

    @contextmanager
    def save_number_of_currently_executing_cell(self) -> Generator[None, None, None]:
        self.cell_counter = shell().execution_count
        try:
            yield
        finally:
            self.cell_counter = None

    @contextmanager
    def ast_transformer_context(
        self, transformers: List[ast.NodeTransformer]
    ) -> Generator[None, None, None]:
        old = shell().ast_transformers
        shell().ast_transformers = old + transformers
        try:
            yield
        finally:
            shell().ast_transformers = old

    @contextmanager
    def input_transformer_context(
        self, transformers: List[Callable[[List[str]], List[str]]]
    ) -> Generator[None, None, None]:
        old = shell().input_transformers_post
        shell().input_transformers_post = old + transformers
        try:
            yield
        finally:
            shell().input_transformers_post = old


_IPY = _IpythonState()


@contextmanager
def save_number_of_currently_executing_cell() -> Generator[None, None, None]:
    with _IPY.save_number_of_currently_executing_cell():
        yield


@contextmanager
def ast_transformer_context(transformers) -> Generator[None, None, None]:
    with _IPY.ast_transformer_context(transformers):
        yield


@contextmanager
def input_transformer_context(transformers) -> Generator[None, None, None]:
    with _IPY.input_transformer_context(transformers):
        yield


def cell_counter() -> int:
    if _IPY.cell_counter is None:
        raise ValueError("should be inside context manager here")
    return _IPY.cell_counter


def run_cell(cell, **kwargs) -> ExecutionResult:
    return shell().run_cell(
        cell,
        store_history=kwargs.pop("store_history", True),
        silent=kwargs.pop("silent", False),
    )


_PURPLE = "\033[95m"
_RED = "\033[91m"
_RESET = "\033[0m"


# allow exceptions for the test_no_prints test
print_ = print


def print_purple(text: str, **kwargs) -> None:
    # The ANSI escape code for purple text is \033[95m
    # The \033 is the escape code, and [95m specifies the color (purple)
    # Reset code is \033[0m that resets the style to default
    print_(f"{_PURPLE}{text}{_RESET}", **kwargs)


def print_red(text: str, **kwargs) -> None:
    print_(f"{_RED}{text}{_RESET}", **kwargs)


def make_mro_inserter_metaclass(old_class, new_class):
    class MetaMroInserter(MetaHasTraits):
        def mro(cls):
            ret = []
            for clazz in super().mro():
                if clazz is old_class:
                    ret.append(new_class)
                ret.append(clazz)
            return ret

    return MetaMroInserter
