# -*- coding: future_annotations -*-
from typing import Any, List, Optional


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
