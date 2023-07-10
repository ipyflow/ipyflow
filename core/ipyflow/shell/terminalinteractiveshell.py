# -*- coding: utf-8 -*-
from IPython.terminal.interactiveshell import TerminalInteractiveShell

from ipyflow import singletons
from ipyflow.shell.interactiveshell import UsesIPyflowShell


class IPyflowTerminalInteractiveShell(
    singletons.IPyflowShell, TerminalInteractiveShell, metaclass=UsesIPyflowShell  # type: ignore
):
    pass
