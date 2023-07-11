# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

from ipyflow import singletons
from ipyflow.shell.interactiveshell import IPyflowInteractiveShell, UsesIPyflowShell
from ipyflow.shell.interactiveshellembed import IPyflowInteractiveShellEmbed, embed
from ipyflow.shell.terminalinteractiveshell import IPyflowTerminalInteractiveShell
from ipyflow.shell.zmqshell import IPyflowZMQInteractiveShell

if TYPE_CHECKING:
    from IPython import InteractiveShell

__all__ = [
    "embed",
    "IPyflowInteractiveShell",
    "IPyflowInteractiveShellEmbed",
    "IPyflowTerminalInteractiveShell",
    "IPyflowZMQInteractiveShell",
]


def load_ipython_extension(ipy: "InteractiveShell") -> None:
    cur_shell_cls = ipy.__class__  # type: ignore
    if issubclass(cur_shell_cls, IPyflowInteractiveShell):
        cur_shell_cls.replacement_class = None  # type: ignore
    else:

        class GeneratedIPyflowShell(singletons.IPyflowShell, cur_shell_cls, metaclass=UsesIPyflowShell):  # type: ignore
            pass

        GeneratedIPyflowShell.inject(prev_shell_class=cur_shell_cls)  # type: ignore


def unload_ipython_extension(ipy: "InteractiveShell") -> None:
    assert isinstance(ipy, IPyflowInteractiveShell)  # type: ignore
    cur_shell_cls = ipy.__class__
    assert cur_shell_cls.prev_shell_class is not None  # type: ignore
    cur_shell_cls.replacement_class = cur_shell_cls.prev_shell_class  # type: ignore
