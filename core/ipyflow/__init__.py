# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

import ipyflow.api
from ipyflow.api import *
from ipyflow.kernel import IPyflowKernel
from ipyflow.models import cells, namespaces, scopes, statements, symbols, timestamps
from ipyflow.singletons import flow, tracer

if TYPE_CHECKING:
    from IPython import InteractiveShell


# Jupyter Extension points
def _jupyter_nbextension_paths():
    return [
        {
            "section": "notebook",
            # the path is relative to the `my_fancy_module` directory
            "src": "resources/nbextension",
            # directory in the `nbextension/` namespace
            "dest": "ipyflow",
            # _also_ in the `nbextension/` namespace
            "require": "ipyflow/index",
        }
    ]


def _jupyter_server_extension_paths():
    return [{"module": "ipyflow"}]


def _jupyter_server_extension_points():
    return [{"module": "ipyflow"}]


def load_jupyter_server_extension(nbapp):
    from ipyflow.kernel.kernel import patch_jupyter_taskrunner_run

    patch_jupyter_taskrunner_run()


def load_ipython_extension(ipy: "InteractiveShell") -> None:
    cur_kernel_cls = ipy.kernel.__class__  # type: ignore
    if cur_kernel_cls is IPyflowKernel:
        IPyflowKernel.replacement_class = None  # type: ignore
        return
    IPyflowKernel.inject(prev_kernel_class=cur_kernel_cls)  # type: ignore


def unload_ipython_extension(ipy: "InteractiveShell") -> None:
    assert isinstance(ipy.kernel, IPyflowKernel)  # type: ignore
    assert IPyflowKernel.prev_kernel_class is not None  # type: ignore
    IPyflowKernel.replacement_class = IPyflowKernel.prev_kernel_class  # type: ignore


from . import _version
__version__ = _version.get_versions()['version']


__all__ = ipyflow.api.__all__ + [
    "cells", "namespaces", "scopes", "statements", "symbols", "timestamps"
] + ["__version__"]
