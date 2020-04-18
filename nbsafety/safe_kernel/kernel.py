# -*- coding: utf-8 -*-
from ipykernel.ipkernel import IPythonKernel
import nbsafety.safety
from ..safety import DependencySafety
from ..version import __version__


_SAFETY_STATE = '__SAFETY_STATE'
_CELL_MAGIC_NAME = '__SAFETY_CELL_MAGIC'


class SafeKernel(IPythonKernel):
    implementation = 'safe_kernel'
    implementation_version = __version__

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.shell.run_cell('from {} import {}'.format(
            nbsafety.safety.__name__, DependencySafety.__name__
        ))
        self.shell.run_cell('{} = {}(cell_magic_name="{}")'.format(
            _SAFETY_STATE, DependencySafety.__name__, _CELL_MAGIC_NAME
        ))

    def do_execute(self, code, silent, store_history=True,
                   user_expressions=None, allow_stdin=False):
        code = "%%{}\n{}".format(_CELL_MAGIC_NAME, code)
        return super().do_execute(code, silent, store_history, user_expressions, allow_stdin)
