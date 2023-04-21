# -*- coding: utf-8 -*-
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from typing import Generator, Optional

from ipyflow.utils.misc_utils import yield_in_loop


class DependencyContext(Enum):
    DYNAMIC = "dynamic"
    STATIC = "static"

    @classmethod
    def iter_dep_contexts(cls) -> Generator[None, None, None]:
        for _ in iter_dep_contexts(*cls):
            yield


_dep_ctx_var: ContextVar[Optional[DependencyContext]] = ContextVar(
    "_dep_ctx_var", default=None
)


@contextmanager
def set_dep_context(dep_ctx: DependencyContext) -> Generator[None, None, None]:
    token = _dep_ctx_var.set(dep_ctx)
    try:
        yield
    finally:
        _dep_ctx_var.reset(token)


@contextmanager
def dynamic_context() -> Generator[None, None, None]:
    with set_dep_context(DependencyContext.DYNAMIC):
        yield


@contextmanager
def static_context() -> Generator[None, None, None]:
    with set_dep_context(DependencyContext.STATIC):
        yield


def iter_dep_contexts(*dep_contexts: DependencyContext) -> Generator[None, None, None]:
    for _ in yield_in_loop(*[set_dep_context(dep_ctx) for dep_ctx in dep_contexts]):
        yield
