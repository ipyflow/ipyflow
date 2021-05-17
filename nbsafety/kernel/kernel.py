# -*- coding: future_annotations -*-
import asyncio
import inspect
import sys

from ipykernel.ipkernel import IPythonKernel

from nbsafety.version import __version__
from nbsafety.safety import NotebookSafety
from nbsafety.singletons import nbs


class SafeKernel(IPythonKernel):
    implementation = 'kernel'
    implementation_version = __version__

    def __init__(self, **kwargs):
        original_settrace = sys.settrace
        super().__init__(**kwargs)
        NotebookSafety.instance(use_comm=True, settrace=original_settrace)
        import nest_asyncio
        # ref: https://github.com/erdewit/nest_asyncio
        nest_asyncio.apply()

    def init_metadata(self, parent):
        """
        Don't actually change the metadata; we just want to get the cell id
        out of the execution request.
        """
        cell_id = parent.get('metadata', {}).get('cellId', None)
        if cell_id is not None:
            nbs().set_active_cell(cell_id, position_idx=None)
        return super().init_metadata(parent)

    if inspect.iscoroutinefunction(IPythonKernel.do_execute):
        async def do_execute(self, code, silent, store_history=False, user_expressions=None, allow_stdin=False):
            super_ = super()

            async def _run_cell_func(cell):
                return await super_.do_execute(cell, silent, store_history, user_expressions, allow_stdin)

            if silent:
                # then it's probably a control message; don't run through nbsafety
                return await _run_cell_func(code)
            else:
                return await nbs().safe_execute(code, True, _run_cell_func)
    else:
        def do_execute(self, code, silent, store_history=False, user_expressions=None, allow_stdin=False):
            super_ = super()

            def _run_cell_func(cell):
                return super_.do_execute(cell, silent, store_history, user_expressions, allow_stdin)
            return next(iter(asyncio.get_event_loop().run_until_complete(
                asyncio.wait([nbs().safe_execute(code, False, _run_cell_func)])
            )[0])).result()
