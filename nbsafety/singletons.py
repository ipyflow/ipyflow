# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

import pyccolo as pyc
from traitlets.config.configurable import SingletonConfigurable

if TYPE_CHECKING:
    from nbsafety.kernel.kernel import SafeKernelMixin as SafeKernelInstance
    from nbsafety.safety import NotebookSafety as NotebookSafetyInstance
    from nbsafety.tracing.nbsafety_tracer import SafetyTracer as TracerInstance


class NotebookSafety(SingletonConfigurable):
    _Xyud34_INSTANCE = None

    def __init__(self):
        super().__init__()
        # we need to keep another ref around for some reason to prevent a segfault
        # TODO: figure out why
        self.__class__._Xyud34_INSTANCE = self


class SafeKernel(SingletonConfigurable):
    pass


class SingletonBaseTracer(pyc.BaseTracer):
    pass


def kernel() -> "SafeKernelInstance":
    assert SafeKernel.initialized()
    return SafeKernel.instance()


def nbs() -> "NotebookSafetyInstance":
    assert NotebookSafety.initialized()
    return NotebookSafety.instance()


def tracer() -> "TracerInstance":
    assert SingletonBaseTracer.initialized()
    return SingletonBaseTracer.instance()
