# -*- coding: utf-8 -*-
import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, NamedTuple, Optional
from typing import Type as TypeType

from ipykernel.ipkernel import IPythonKernel
from IPython import get_ipython
from traitlets import Type

from ipyflow import singletons
from ipyflow.data_model.code_cell import CodeCell
from ipyflow.flow import NotebookFlow
from ipyflow.shell.zmqshell import IPyflowZMQInteractiveShell
from ipyflow.version import __version__

if TYPE_CHECKING:
    from ipykernel.comm import Comm

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class PyccoloKernelSettings(NamedTuple):
    store_history: bool


def patched_taskrunner_run(_self, coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Workaround for bugs.python.org/issue39529.
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    from ipyflow.kernel import patched_nest_asyncio

    patched_nest_asyncio.apply(loop)
    future = asyncio.ensure_future(coro, loop=loop)
    try:
        return loop.run_until_complete(future)
    except BaseException as e:
        future.cancel()
        raise e


def patch_jupyter_taskrunner_run():
    # workaround for the issue described in
    # https://github.com/jupyter/notebook/issues/6721
    try:
        import jupyter_core.utils

        jupyter_core.utils._TaskRunner.run = patched_taskrunner_run
    except:  # noqa: E722
        pass


class IPyflowKernel(IPythonKernel):  # type: ignore
    implementation = "kernel"
    shell_class = Type(IPyflowZMQInteractiveShell)
    implementation_version = __version__
    prev_kernel_class: Optional[TypeType[IPythonKernel]] = None
    replacement_class: Optional[TypeType[IPythonKernel]] = None
    client_comm: Optional["Comm"] = None

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        self._initialize()

    @classmethod
    def instance(cls, *args, **kwargs) -> "IPyflowKernel":
        ret = super().instance(*args, **kwargs)
        NotebookFlow.instance().register_comm_target(ret)
        return ret

    def _initialize(self) -> None:
        from ipyflow.kernel import patched_nest_asyncio

        patched_nest_asyncio.apply()

        # As of 2023/05/21, it seems like this is only necessary in
        # the server extension, but seems like it can't hurt to do
        # it here as well.
        patch_jupyter_taskrunner_run()

    @classmethod
    def inject(zmq_kernel_class, prev_kernel_class: TypeType[IPythonKernel]) -> None:
        ipy = get_ipython()
        kernel = ipy.kernel
        kernel.__class__ = zmq_kernel_class
        if zmq_kernel_class.prev_kernel_class is None:
            kernel._initialize()
            kernel.after_init_class()
            for subclass in singletons.IPyflowKernel._walk_mro():
                subclass._instance = kernel
        zmq_kernel_class.prev_kernel_class = prev_kernel_class
        CodeCell._cell_counter = ipy.execution_count

    @classmethod
    def _maybe_eject(zmq_kernel_class) -> None:
        if zmq_kernel_class.replacement_class is None:
            return
        get_ipython().kernel.__class__ = zmq_kernel_class.replacement_class
        zmq_kernel_class.replacement_class = None

    def before_init_metadata(self, parent) -> None:
        """
        Don't actually change the metadata; we just want to get the cell id
        out of the execution request.
        """
        flow_ = singletons.flow()
        metadata = parent.get("metadata", {})
        cell_id = metadata.get("cellId", None)
        if cell_id is not None:
            flow_.set_active_cell(cell_id)
        tags = tuple(metadata.get("tags", ()))
        flow_.set_tags(tags)

    def init_metadata(self, parent):
        """
        Don't actually change the metadata; we just want to get the cell id
        out of the execution request.
        """
        self.before_init_metadata(parent)
        return super().init_metadata(parent)

    if inspect.iscoroutinefunction(IPythonKernel.do_execute):

        async def do_execute(
            self,
            code,
            silent,
            store_history=False,
            user_expressions=None,
            allow_stdin=False,
            cell_id=None,
        ):
            ret = await super().do_execute(
                code,
                silent,
                store_history,
                user_expressions,
                allow_stdin,
                cell_id=cell_id,
            )
            self._maybe_eject()
            return ret

    else:

        def do_execute(
            self,
            code,
            silent,
            store_history=False,
            user_expressions=None,
            allow_stdin=False,
            cell_id=None,
        ):
            async def _run_cell_func(cell):
                ret = super().do_execute(
                    cell,
                    silent,
                    store_history,
                    user_expressions,
                    allow_stdin,
                    cell_id=cell_id,
                )
                if inspect.isawaitable(ret):
                    return await ret
                else:
                    return ret

            result = asyncio.get_event_loop().run_until_complete(_run_cell_func(code))
            self._maybe_eject()
            return result
