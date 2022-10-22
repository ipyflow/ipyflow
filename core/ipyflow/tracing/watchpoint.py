# -*- coding: utf-8 -*-
from typing import Any, Callable, Optional, Tuple


class Watchpoint:
    def __init__(self, name: str, pred: Optional[Callable[..., bool]]) -> None:
        self.name = name
        self.pred = pred

    def __call__(
        self, obj: Any, *, position: Tuple[int, int], symbol_name: str
    ) -> bool:
        return (
            True
            if self.pred is None
            else self.pred(obj, position=position, symbol_name=symbol_name)
        )

    def __repr__(self):
        name_str = (
            "<anonymous-watchpoint>"
            if self.name is None
            else f"<watchpoint-{self.name}>"
        )
        pred_str = (
            "no predicate" if self.pred is None else "predicate " + repr(self.pred)
        )
        return f"{name_str} ({pred_str})"


class Watchpoints(list):
    def append(self, *args, **kwargs) -> None:
        raise NotImplementedError("please use the `add` method instead")

    def extend(self, *args, **kwargs) -> None:
        raise NotImplementedError("please use the `add` method instead")

    def __add__(self, *args, **kwargs) -> None:
        raise NotImplementedError("please use the `add` method instead")

    def __iadd__(self, *args, **kwargs) -> None:
        raise NotImplementedError("please use the `add` method instead")

    def __radd__(self, *args, **kwargs) -> None:
        raise NotImplementedError("please use the `add` method instead")

    def add(
        self, pred: Optional[Callable[..., bool]] = None, name: Optional[str] = None
    ):
        super().append(Watchpoint(name, pred))

    def passing(
        self, obj: Any, *, position: Tuple[int, int], symbol_name: str
    ) -> Tuple[Watchpoint, ...]:
        passing_watchpoints = []
        for wp in self:
            if wp(obj, position=position, symbol_name=symbol_name):
                passing_watchpoints.append(wp)
        return tuple(passing_watchpoints)

    def __call__(
        self, obj: Any, *, position: Tuple[int, int], symbol_name: str
    ) -> Tuple[Watchpoint, ...]:
        return self.passing(obj, position=position, symbol_name=symbol_name)
