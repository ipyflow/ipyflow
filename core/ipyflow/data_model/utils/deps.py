# -*- coding: utf-8 -*-
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from typing import Generator, Optional


class Dependency(Enum):
    DYNAMIC = "dynamic"
    STATIC = "static"


dep_ctx: ContextVar[Optional[Dependency]] = ContextVar("dep_ctx", default=None)


@contextmanager
def set_dep_ctx(dep_type: Dependency) -> Generator[None, None, None]:
    token = dep_ctx.set(dep_type)
    try:
        yield
    finally:
        dep_ctx.reset(token)


@contextmanager
def dynamic_context() -> Generator[None, None, None]:
    with set_dep_ctx(Dependency.DYNAMIC):
        yield


@contextmanager
def static_context() -> Generator[None, None, None]:
    with set_dep_ctx(Dependency.STATIC):
        yield
