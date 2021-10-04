# -*- coding: future_annotations -*-
from collections import defaultdict
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from typing import Any, List, Mapping, Optional, Sequence


class KeyDict(dict):
    def __missing__(self, key):
        return key


class GetterFallback:
    def __init__(self, stages: Sequence[Mapping[Any, Any]]):
        self.stages = stages

    def __getitem__(self, item):
        for stage in self.stages:
            if item in stage:
                return stage[item]
        raise KeyError()

    def __setitem__(self, key, value):
        return NotImplemented

    def __contains__(self, item):
        for stage in self.stages:
            if item in stage:
                return True
        return False
