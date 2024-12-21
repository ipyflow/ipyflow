# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from IPython import InteractiveShell


def load_ipython_extension(shell: "InteractiveShell") -> None:
    shell.run_line_magic("load_ext", "ipyflow.shell")
    shell.run_line_magic(
        "flow", "deregister_tracer ipyflow.tracing.ipyflow_tracer.DataflowTracer"
    )
