# -*- coding: utf-8 -*-
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from typing import Generator, Optional

from ipyflow.utils.misc_utils import yield_in_loop


class Dependency(Enum):
    DYNAMIC = "dynamic"
    STATIC = "static"

    @classmethod
    def iter_dep_contexts(cls) -> Generator[None, None, None]:
        for _ in iter_dep_contexts(*cls):
            yield


dep_ctx: ContextVar[Optional[Dependency]] = ContextVar("dep_ctx", default=None)


@contextmanager
def set_dep_context(dep_type: Dependency) -> Generator[None, None, None]:
    token = dep_ctx.set(dep_type)
    try:
        yield
    finally:
        dep_ctx.reset(token)


@contextmanager
def dynamic_context() -> Generator[None, None, None]:
    with set_dep_context(Dependency.DYNAMIC):
        yield


@contextmanager
def static_context() -> Generator[None, None, None]:
    with set_dep_context(Dependency.STATIC):
        yield


def iter_dep_contexts(*dep_types: Dependency) -> Generator[None, None, None]:
    for _ in yield_in_loop(*[set_dep_context(dep_type) for dep_type in dep_types]):
        yield
