# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

import pyccolo as pyc
from traitlets.config.configurable import SingletonConfigurable

if TYPE_CHECKING:
    from ipyflow.flow import NotebookFlow as NotebookFlowInstance
    from ipyflow.kernel.kernel import IPyflowKernelBase as IPyflowKernelInstance
    from ipyflow.tracing.ipyflow_tracer import DataflowTracer as TracerInstance


class NotebookFlow(SingletonConfigurable):
    _Xyud34_INSTANCE = None

    def __init__(self):
        super().__init__()
        # we need to keep another ref around for some reason to prevent a segfault
        # TODO: figure out why
        self.__class__._Xyud34_INSTANCE = self


class IPyflowKernel(SingletonConfigurable):
    pass


class SingletonBaseTracer(pyc.BaseTracer):
    pass


def kernel() -> "IPyflowKernelInstance":
    assert IPyflowKernel.initialized()
    return IPyflowKernel.instance()


def flow() -> "NotebookFlowInstance":
    assert NotebookFlow.initialized()
    return NotebookFlow.instance()


def tracer() -> "TracerInstance":
    assert SingletonBaseTracer.initialized()
    return SingletonBaseTracer.instance()
