# -*- coding: utf-8 -*-
from ipykernel.ipkernel import IPythonKernel
import nbsafety.safety
from ..safety import DependencySafety
from ..version import __version__
# import warnings
#Avoid networkx gives a FutureWarning
# TODO (smacke): this disables all FutureWarnings; disable until we can figure out a better system
# warnings.filterwarnings("ignore", category=FutureWarning)

_SAFETY_STATE = '__SAFETY_STATE'
_CELL_MAGIC_NAME = '__SAFETY_CELL_MAGIC'
_LINE_MAGIC_NAME = '__SAFETY_LINE_MAGIC'


class SafeKernel(IPythonKernel):
    implementation = 'kernel'
    implementation_version = __version__

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._init_safety()

    def _init_safety(self):
        self.shell.run_cell(f'from {nbsafety.safety.__name__} import {DependencySafety.__name__}')
        self.shell.run_cell(
            f'{_SAFETY_STATE} = {DependencySafety.__name__}'
            f'(cell_magic_name="{_CELL_MAGIC_NAME}",'
            f' line_magic_name="{_LINE_MAGIC_NAME}",'
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
        if code.split()[0] == "%safety":
            code = "%{} {}".format(_LINE_MAGIC_NAME, ' '.join(code.split()[1:]))
            return super().do_execute(code, silent, False, user_expressions, allow_stdin)
        code = f"%%{_CELL_MAGIC_NAME}\n{code}"
        return super().do_execute(code, silent, False, user_expressions, allow_stdin)
