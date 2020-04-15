from ipykernel.ipkernel import IPythonKernel
from ..version import __version__


class SafeKernel(IPythonKernel):
    implementation = 'safe_kernel'
    implementation_version = __version__

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.shell.run_cell('from nbsafety.project_04062020 import dependency_safety')

    def do_execute(self, code, silent, store_history=True,
                   user_expressions=None, allow_stdin=False):
        code = "%%dependency_safety\n" + code
        return super().do_execute(code, silent, store_history, user_expressions, allow_stdin)
