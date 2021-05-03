# -*- coding: future_annotations -*-
from ipykernel.ipkernel import IPythonKernel
from nbsafety.version import __version__
from nbsafety.safety import NotebookSafety
from nbsafety.singletons import nbs


class SafeKernel(IPythonKernel):
    implementation = 'kernel'
    implementation_version = __version__

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        NotebookSafety.instance(use_comm=True)

    def init_metadata(self, parent):
        """
        Don't actually change the metadata; we just want to get the cell id
        out of the execution request.
        """
        nbs().set_active_cell(parent['metadata']['cellId'], position_idx=None)
        return super().init_metadata(parent)

    def do_execute(self, code, silent, store_history=False, user_expressions=None, allow_stdin=False):
        super_ = super()

        def _run_cell_func(cell):
            return super_.do_execute(cell, silent, store_history, user_expressions, allow_stdin)
        return nbs().safe_execute(code, _run_cell_func)
