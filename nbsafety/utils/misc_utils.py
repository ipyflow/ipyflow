# -*- coding: future_annotations -*-
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from typing import Any, List, Mapping, Optional, Sequence


class KeyDict(dict):
    def __missing__(self, key):
        return key


class GetterPipeline:
    def __init__(self, stages: Optional[List[Any]] = None):
        if stages is None:
            stages = []
        self.stages = stages

    def __ior__(self, stage):
        self.stages.append(stage)

    def __or__(self, stage):
        return GetterPipeline(self.stages + [stage])

    def __ror__(self, stage):
        return GetterPipeline([stage] + self.stages)

    def __getitem__(self, item):
        for stage in self.stages:
            item = stage[item]
        return item

    def keys(self):
        return self.stages[0].keys()

    def items(self):
        for k in self.keys():
            yield k, self[k]

    def values(self):
        for _, v in self.items():
            yield v


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
