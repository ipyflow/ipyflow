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
from ipyflow.flow import NotebookFlow
from ipyflow.shell.zmqshell import IPyflowZMQInteractiveShell
from ipyflow.singletons import flow
from ipyflow.utils.ipython_utils import make_mro_inserter_metaclass
from ipyflow.utils.misc_utils import is_project_file
from ipyflow.version import __version__

if TYPE_CHECKING:
    from ipykernel.comm import Comm

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class PyccoloKernelSettings(NamedTuple):
    store_history: bool


def patch_pydevd_file_filters() -> None:
    try:
        from _pydevd_bundle.pydevd_filtering import FilesFiltering

        orig_in_project_roots = FilesFiltering.in_project_roots

        def in_project_roots(self, received_filename):
            if is_project_file(received_filename):
                return False
            return orig_in_project_roots(self, received_filename)

        FilesFiltering.in_project_roots = in_project_roots
    except Exception:
        pass


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
    except BaseException:
        future.cancel()
        raise


def patch_jupyter_taskrunner_run():
    # workaround for the issue described in
    # https://github.com/jupyter/notebook/issues/6721
    try:
        import jupyter_core.utils

        jupyter_core.utils._TaskRunner.run = patched_taskrunner_run
    except Exception:
        pass


class IPyflowKernel(singletons.IPyflowKernel, IPythonKernel):  # type: ignore
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
        flow_ = NotebookFlow.instance()
        flow_.register_comm_target(ret)
        try:
            from superduperreload import ModuleReloader

            ModuleReloader.instance(shell=ret.shell, flow=flow_, enabled=False)
        except Exception:
            pass
        return ret

    def _initialize(self, do_asyncio_patches: bool = False) -> None:
        if do_asyncio_patches:
            from ipyflow.kernel import patched_nest_asyncio

            patched_nest_asyncio.apply()

            # As of 2023/05/21, it seems like this is only necessary in
            # the server extension, but seems like it can't hurt to do
            # it here as well.
            patch_jupyter_taskrunner_run()
        patch_pydevd_file_filters()
        self._has_cell_id: bool = (
            "cell_id" in inspect.signature(super().do_execute).parameters
        )

    async def do_debug_request(self, msg):
        flow_ = flow()
        settings = flow_.mut_settings
        if msg.get("command") == "attach":
            flow_.handle(
                {
                    "type": "compute_exec_schedule",
                    "cell_metadata_by_id": {},
                    "notify_content_changed": False,
                }
            )
            settings.dataflow_enabled = False
        elif msg.get("command") == "disconnect":
            settings.dataflow_enabled = True
            flow_.handle({"type": "compute_exec_schedule"})
        return await super().do_debug_request(msg)

    @classmethod
    def inject(
        kernel_class,
        prev_kernel_class: TypeType[IPythonKernel],
        do_asyncio_patches: bool = False,
    ) -> None:
        ipy = get_ipython()
        kernel = ipy.kernel
        kernel.__class__ = kernel_class
        if kernel_class.prev_kernel_class is None:
            kernel._initialize(do_asyncio_patches=do_asyncio_patches)
            for subclass in singletons.IPyflowKernel._walk_mro():
                subclass._instance = kernel
        NotebookFlow.instance().register_comm_target(kernel)
        kernel_class.prev_kernel_class = prev_kernel_class

    @classmethod
    def _maybe_eject(kernel_class) -> None:
        if kernel_class.replacement_class is None:
            return
        get_ipython().kernel.__class__ = kernel_class.replacement_class
        kernel_class.replacement_class = None

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
            **kwargs,
        ):
            super_ = super()
            if self._has_cell_id:
                kwargs["cell_id"] = cell_id
            ret = await super_.do_execute(
                code,
                silent,
                store_history,
                user_expressions,
                allow_stdin,
                **kwargs,
            )
            self._maybe_eject()
            return ret

    else:
        from ipyflow.kernel import patched_nest_asyncio

        patched_nest_asyncio.apply()

        def do_execute(
            self,
            code,
            silent,
            store_history=False,
            user_expressions=None,
            allow_stdin=False,
            cell_id=None,
            **kwargs,
        ):
            super_ = super()
            if self._has_cell_id:
                kwargs["cell_id"] = cell_id

            async def _run_cell_func(cell):
                ret = super_.do_execute(
                    cell,
                    silent,
                    store_history,
                    user_expressions,
                    allow_stdin,
                    **kwargs,
                )
                if inspect.isawaitable(ret):
                    return await ret
                else:
                    return ret

            result = asyncio.get_event_loop().run_until_complete(_run_cell_func(code))
            self._maybe_eject()
            return result


UsesIPyflowKernel = make_mro_inserter_metaclass(IPythonKernel, IPyflowKernel)
