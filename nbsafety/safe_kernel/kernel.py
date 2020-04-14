from ipykernel.ipkernel import IPythonKernel
from nbsafety.version import __version__


class SafeKernel(IPythonKernel):
    implementation = 'safe_kernel'
    implementation_version = __version__

    def __init__(self, **kwargs):
        super(self.__class__, self).__init__(**kwargs)

    def do_execute(self, code, silent, store_history=True,
                   user_expressions=None, allow_stdin=False):
        print('hello from safe kernel')
        reply = super(self.__class__, self).do_execute(code, silent, store_history, user_expressions, allow_stdin)
        return reply
