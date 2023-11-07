# -*- coding: utf-8 -*-
import re
from threading import Timer
from typing import Callable


class KeyDict(dict):
    def __missing__(self, key):
        return key


def cleanup_discard(d, key, val):
    s = d.get(key, set())
    s.discard(val)
    if len(s) == 0:
        d.pop(key, None)


def cleanup_pop(d, key, val):
    d2 = d.get(key, {})
    d2.pop(val, None)
    if len(d2) == 0:
        d.pop(key, None)


def debounce(wait: float) -> Callable[[Callable[..., None]], Callable[..., bool]]:
    """Decorator that will postpone a functions
    execution until after wait seconds
    have elapsed since the last time it was invoked."""

    def decorator(fn: Callable[..., None]) -> Callable[..., bool]:
        def debounced(*args, **kwargs) -> bool:
            def call_it():
                fn(*args, **kwargs)

            try:
                did_start_new = debounced.t.finished.is_set()  # type: ignore
                debounced.t.cancel()  # type: ignore
            except AttributeError:
                did_start_new = True
            debounced.t = Timer(wait, call_it)  # type: ignore
            debounced.t.start()  # type: ignore
            return did_start_new

        return debounced

    return decorator


def yield_in_loop(*gens):
    for gen in gens:
        with gen:
            yield


_PROJECT_FILE_REGEX = re.compile(r"[/\\](ipyflow|pyccolo)[/\\]")


def is_project_file(filename: str) -> bool:
    return bool(_PROJECT_FILE_REGEX.search(filename))
