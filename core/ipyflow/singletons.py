# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

import pyccolo as pyc
from IPython.core.interactiveshell import InteractiveShellABC
from traitlets.config.configurable import SingletonConfigurable

if TYPE_CHECKING:
    from ipyflow.flow import NotebookFlow as NotebookFlowInstance
    from ipyflow.kernel.kernel import IPyflowKernel as IPyflowKernelInstance
    from ipyflow.shell.interactiveshell import (
        IPyflowInteractiveShell as IPyflowShellInstance,
    )
    from ipyflow.tracing.ipyflow_tracer import DataflowTracer as TracerInstance


class NotebookFlow(SingletonConfigurable):
    _Xyud34_INSTANCE = None

    def __init__(self):
        super().__init__()
        # we need to keep another ref around for some reason to prevent a segfault
        # TODO: figure out why
        self.__class__._Xyud34_INSTANCE = self


class IPyflowShell(SingletonConfigurable):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        InteractiveShellABC.register(cls)


class IPyflowKernel(SingletonConfigurable):
    pass


class SingletonBaseTracer(pyc.BaseTracer):
    pass


def shell() -> "IPyflowShellInstance":
    assert IPyflowShell.initialized()
    return IPyflowShell.instance()


def kernel() -> "IPyflowKernelInstance":
    assert IPyflowKernel.initialized()
    return IPyflowKernel.instance()


def flow() -> "NotebookFlowInstance":
    assert NotebookFlow.initialized()
    return NotebookFlow.instance()


def tracer() -> "TracerInstance":
    assert SingletonBaseTracer.initialized()
    return SingletonBaseTracer.instance()
