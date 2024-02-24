# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

import ipyflow.api
from ipyflow import singletons
from ipyflow.api import *  # noqa: F403
from ipyflow.kernel.kernel import IPyflowKernel, UsesIPyflowKernel
from ipyflow.shell import load_ipython_extension as load_ipyflow_extension, unload_ipython_extension as unload_ipyflow_extension
from ipyflow.models import cell_above, cell_below, cell_at_offset, cells, last_run_cell, namespaces, scopes, statements, symbols, timestamps
from ipyflow.singletons import flow, kernel, shell, tracer
from ipyflow.tracing.uninstrument import uninstrument

from . import _version
__version__ = _version.get_versions()['version']

if TYPE_CHECKING:
    from IPython import InteractiveShell


def _jupyter_server_extension_paths():
    return [{"module": "ipyflow"}]


def _jupyter_server_extension_points():
    return [{"module": "ipyflow"}]


def load_jupyter_server_extension(nbapp):
    from ipyflow.kernel.kernel import patch_jupyter_taskrunner_run

    patch_jupyter_taskrunner_run()


def load_ipython_extension(ipy: "InteractiveShell", do_asyncio_patches: bool = False) -> None:
    load_ipyflow_extension(ipy)
    kernel = getattr(ipy, "kernel", None)
    if kernel is None:
        return
    cur_kernel_cls = kernel.__class__  # type: ignore
    if issubclass(cur_kernel_cls, IPyflowKernel):
        cur_kernel_cls.replacement_class = None  # type: ignore
    else:
        class GeneratedIPyflowKernel(singletons.IPyflowKernel, cur_kernel_cls, metaclass=UsesIPyflowKernel):  # type: ignore
            pass
        GeneratedIPyflowKernel.inject(prev_kernel_class=cur_kernel_cls, do_asyncio_patches=do_asyncio_patches)  # type: ignore

    if IPyflowKernel.client_comm is None:  # type: ignore
        from ipykernel.comm import Comm

        comm = Comm(target_name="ipyflow-client")  # type: ignore
        comm.comm_id = "ipyflow-client"  # type: ignore
        IPyflowKernel.client_comm = comm  # type: ignore
    IPyflowKernel.client_comm.send({"type": "establish", "success": True})  # type: ignore


def unload_ipython_extension(ipy: "InteractiveShell") -> None:
    unload_ipyflow_extension(ipy)
    kernel = getattr(ipy, "kernel", None)
    if kernel is None:
        return
    cur_kernel_cls = kernel.__class__
    assert issubclass(cur_kernel_cls, IPyflowKernel)  # type: ignore
    assert cur_kernel_cls.prev_kernel_class is not None  # type: ignore
    cur_kernel_cls.replacement_class = cur_kernel_cls.prev_kernel_class  # type: ignore

    # TODO: reset state here so that %reload_ext behaves like unload then load?

    if IPyflowKernel.client_comm is not None:  # type: ignore
        IPyflowKernel.client_comm.send({"type": "unestablish", "success": True})  # type: ignore


__all__ = ipyflow.api.__all__ + [
    "__version__",
    "cell_above",
    "cell_below",
    "cell_at_offset",
    "cells",
    "flow",
    "kernel",
    "last_run_cell",
    "namespaces",
    "scopes",
    "shell",
    "statements",
    "symbols",
    "timestamps",
    "tracer",
    "uninstrument",
]


def main():
    import sys
    # Remove the CWD from sys.path while we load stuff.
    # This is added back by InteractiveShellApp.init_path()
    # TODO: probably need to make this separate from ipyflow package so that we can
    #  completely avoid imports until after removing cwd from sys.path
    if sys.path[0] == "":
        del sys.path[0]

    from IPython.terminal import ipapp as app

    from ipyflow.shell import IPyflowTerminalInteractiveShell

    app.launch_new_instance(interactive_shell_class=IPyflowTerminalInteractiveShell)
