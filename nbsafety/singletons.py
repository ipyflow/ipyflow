# -*- coding: future_annotations -*-
from typing import TYPE_CHECKING

from traitlets.config.configurable import SingletonConfigurable

if TYPE_CHECKING:
    from nbsafety.safety import NotebookSafety as NotebookSafetyInstance
    from nbsafety.tracing.trace_manager import TraceManager as TraceManagerInstance


class NotebookSafety(SingletonConfigurable):
    _Xyud34_INSTANCE = None

    def __init__(self):
        super().__init__()
        # we need to keep another ref around for some reason to prevent a segfault
        # TODO: figure out why
        self.__class__._Xyud34_INSTANCE = self


class TraceManager(SingletonConfigurable):
    pass


def nbs() -> NotebookSafetyInstance:
    assert NotebookSafety.initialized()
    return NotebookSafety.instance()


def tracer() -> TraceManagerInstance:
    assert TraceManager.initialized()
    return TraceManager.instance()
