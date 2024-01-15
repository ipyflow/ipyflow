# -*- coding: utf-8 -*-
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from typing import Generator, Optional

from ipyflow.utils.misc_utils import yield_in_loop


class SlicingContext(Enum):
    DYNAMIC = "dynamic"
    STATIC = "static"

    @classmethod
    def iter_slicing_contexts(cls) -> Generator[None, None, None]:
        for _ in iter_slicing_contexts(*cls):
            yield


slicing_ctx_var: ContextVar[Optional[SlicingContext]] = ContextVar(
    "slicing_ctx_var", default=None
)


@contextmanager
def set_slicing_context(dep_ctx: SlicingContext) -> Generator[None, None, None]:
    token = slicing_ctx_var.set(dep_ctx)
    try:
        yield
    finally:
        slicing_ctx_var.reset(token)


@contextmanager
def dynamic_slicing_context() -> Generator[None, None, None]:
    with set_slicing_context(SlicingContext.DYNAMIC):
        yield


@contextmanager
def static_slicing_context() -> Generator[None, None, None]:
    with set_slicing_context(SlicingContext.STATIC):
        yield


@contextmanager
def slicing_context(is_static: bool) -> Generator[None, None, None]:
    with set_slicing_context(
        SlicingContext.STATIC if is_static else SlicingContext.DYNAMIC
    ):
        yield


def iter_slicing_contexts(*dep_contexts: SlicingContext) -> Generator[None, None, None]:
    for _ in yield_in_loop(*[set_slicing_context(dep_ctx) for dep_ctx in dep_contexts]):
        yield
