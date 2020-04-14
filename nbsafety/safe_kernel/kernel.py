from ipykernel.ipkernel import IPythonKernel
from ..version import __version__


preamble = """
import ....
# set up data structures
# whatever else is needed
"""
class SafeKernel(IPythonKernel):
    implementation = 'safe_kernel'
    implementation_version = __version__

    def __init__(self, **kwargs):
        super(self.__class__, self).__init__(**kwargs)

    def do_execute(self, code, silent, store_history=True,
                   user_expressions=None, allow_stdin=False):
        self.shell.run_cell('x=3+4', silent=True)
        self.shell.run_cell('3+x')
        reply = super(self.__class__, self).do_execute(code, silent, store_history, user_expressions, allow_stdin)
        return reply
