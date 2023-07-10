# -*- coding: utf-8 -*-
from ipykernel.zmqshell import ZMQInteractiveShell

from ipyflow import singletons
from ipyflow.shell.interactiveshell import UsesIPyflowShell


class IPyflowZMQInteractiveShell(
    singletons.IPyflowShell, ZMQInteractiveShell, metaclass=UsesIPyflowShell  # type: ignore
):
    pass
