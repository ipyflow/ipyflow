# -*- coding: utf-8 -*-
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


def debounce(wait: float) -> Callable[[Callable[..., None]], Callable[..., None]]:
    """Decorator that will postpone a functions
    execution until after wait seconds
    have elapsed since the last time it was invoked."""

    def decorator(fn: Callable[..., None]) -> Callable[..., None]:
        def debounced(*args, **kwargs) -> None:
            def call_it():
                fn(*args, **kwargs)

            try:
                debounced.t.cancel()  # type: ignore
            except (AttributeError):
                pass
            debounced.t = Timer(wait, call_it)  # type: ignore
            debounced.t.start()  # type: ignore

        return debounced

    return decorator
