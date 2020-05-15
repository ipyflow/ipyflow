# -*- coding: utf-8 -*-
from ipykernel.ipkernel import IPythonKernel
from ..version import __version__
# import warnings
#Avoid networkx gives a FutureWarning
# TODO (smacke): this disables all FutureWarnings; disable until we can figure out a better system
# warnings.filterwarnings("ignore", category=FutureWarning)

_SAFETY_STATE = '__SAFETY_STATE'
_CELL_MAGIC_NAME = '__SAFETY_CELL_MAGIC'


from ipykernel.comm import Comm
from traitlets import Instance, default
class SafetyComm(Comm):
    kernel = Instance('nbsafety.kernel.kernel.SafeKernel', allow_none=True)

    @default('kernel')
    def _default_kernel(self):
        if SafeKernel.initialized():
            return SafeKernel.instance()


class SafeKernel(IPythonKernel):
    implementation = 'kernel'
    implementation_version = __version__

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._inited = False

    def _init_safety(self):
        import nbsafety.safety
        from ..safety import DependencySafety
        self.shell.run_cell(f'from {nbsafety.safety.__name__} import {DependencySafety.__name__}')
        self.shell.run_cell('from ipykernel.comm import Comm')
        self.shell.run_cell('_SAFETY_COMM = Comm(target_name="nbsafety", data={})')
        self.shell.run_cell(
            f'{_SAFETY_STATE} = {DependencySafety.__name__}'
            f'(cell_magic_name="{_CELL_MAGIC_NAME}",'
            f' comm=_SAFETY_COMM,'
            f' store_history=False'
            f')'
        )
        # hack to init logging
        # TODO: figure out why this is necessary
        self.do_execute("""
def _foo():
    pass
_foo()  # for some reason the fn call is necessary to properly init logging
del _foo
""", False)
        self.shell.run_cell(f'{_SAFETY_STATE}._logging_inited()')

    def do_execute(self, code, silent, store_history=False,
                   user_expressions=None, allow_stdin=False):
        if not self._inited:
            self._inited = True
            self._init_safety()
        code = f"%%{_CELL_MAGIC_NAME}\n{code}"
        return super().do_execute(code, silent, False, user_expressions, allow_stdin)
