# -*- coding: utf-8 -*-
import asyncio
import inspect
import sys

from ipykernel.ipkernel import IPythonKernel

from nbsafety.version import __version__
from nbsafety.safety import NotebookSafety
from nbsafety.singletons import nbs


class SafeKernel(IPythonKernel):
    implementation = "kernel"
    implementation_version = __version__

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        NotebookSafety.instance(use_comm=True)
        import nest_asyncio

        # ref: https://github.com/erdewit/nest_asyncio
        nest_asyncio.apply()

    def init_metadata(self, parent):
        """
        Don't actually change the metadata; we just want to get the cell id
        out of the execution request.
        """
        metadata = parent.get("metadata", {})
        cell_id = metadata.get("cellId", None)
        if cell_id is not None:
            nbs().set_active_cell(cell_id)
        tags = tuple(metadata.get("tags", ()))
        nbs().set_tags(tags)
        return super().init_metadata(parent)

    if inspect.iscoroutinefunction(IPythonKernel.do_execute):

        async def do_execute(
            self,
            code,
            silent,
            store_history=False,
            user_expressions=None,
            allow_stdin=False,
        ):
            super_ = super()

            async def _run_cell_func(cell):
                return await super_.do_execute(
                    cell, silent, store_history, user_expressions, allow_stdin
                )

            if silent:
                # then it's probably a control message; don't run through nbsafety
                return await _run_cell_func(code)
            else:
                return await nbs().safe_execute(code, True, _run_cell_func)

    else:

        def do_execute(
            self,
            code,
            silent,
            store_history=False,
            user_expressions=None,
            allow_stdin=False,
        ):
            super_ = super()

            async def _run_cell_func(cell):
                ret = super_.do_execute(
                    cell, silent, store_history, user_expressions, allow_stdin
                )
                if inspect.isawaitable(ret):
                    return await ret
                else:
                    return ret

            return asyncio.get_event_loop().run_until_complete(
                nbs().safe_execute(code, True, _run_cell_func)
            )
