# -*- coding: utf-8 -*-
from ipykernel.ipkernel import IPythonKernel
from ..version import __version__
from ..safety import DependencySafety
# import warnings
#Avoid networkx gives a FutureWarning
# TODO (smacke): this disables all FutureWarnings; disable until we can figure out a better system
# warnings.filterwarnings("ignore", category=FutureWarning)


class SafeKernel(IPythonKernel):
    implementation = 'kernel'
    implementation_version = __version__

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._safety = DependencySafety(use_comm=True)

    def do_execute(self, code, silent, store_history=False, user_expressions=None, allow_stdin=False):
        super_ = super()

        def _run_cell_func(cell):
            return super_.do_execute(cell, silent, store_history, user_expressions, allow_stdin)
        return self._safety.safe_execute(code, _run_cell_func)
