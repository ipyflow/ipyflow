# -*- coding: utf-8 -*-
import asyncio
import inspect
import logging
import sys
from contextlib import contextmanager, suppress
from typing import (
    TYPE_CHECKING,
    Callable,
    ContextManager,
    Generator,
    List,
    NamedTuple,
    Optional,
    Tuple,
    Type,
    Union,
)

import pyccolo as pyc
from ipykernel.ipkernel import IPythonKernel
from IPython import get_ipython
from IPython.core.magic import register_cell_magic
from pyccolo.import_hooks import TraceFinder

from ipyflow import singletons
from ipyflow.config import ExecutionMode
from ipyflow.data_model.code_cell import CodeCell
from ipyflow.flow import NotebookFlow
from ipyflow.tracing.flow_ast_rewriter import DataflowAstRewriter
from ipyflow.tracing.ipyflow_tracer import (
    DataflowTracer,
    ModuleIniter,
    StackFrameManager,
)
from ipyflow.utils.ipython_utils import (
    ast_transformer_context,
    capture_output_tee,
    input_transformer_context,
    run_cell,
    save_number_of_currently_executing_cell,
)
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


class PyccoloKernelHooks:
    def after_init_class(self) -> None:
        ...

    def before_init_metadata(self, parent) -> None:
        ...

    def before_enter_tracing_context(self) -> None:
        ...

    def before_execute(self, cell_content: str) -> Optional[str]:
        ...

    def should_trace(self) -> bool:
        ...

    def inner_tracing_context(self) -> ContextManager[None]:
        ...

    def after_execute(self, cell_content: str) -> None:
        ...

    def on_exception(self, e: Optional[Exception]) -> None:
        ...


class OutputRecorder(pyc.BaseTracer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with self.persistent_fields():
            self.capture_output_tee = capture_output_tee()
        self.capture_output = None

    @pyc.register_raw_handler(pyc.init_module)
    def init_module(self, *_, **__):
        self.capture_output = self.capture_output_tee.__enter__()

    @property
    def should_patch_meta_path(self) -> bool:
        return False


class PyccoloKernelMixin(PyccoloKernelHooks):
    def __init__(self, **kwargs) -> None:
        store_history = kwargs.pop("store_history", True)
        super().__init__(**kwargs)
        self._initialize(store_history=store_history)

    def _initialize(self, store_history: bool = True) -> None:
        self.settings: PyccoloKernelSettings = PyccoloKernelSettings(
            store_history=store_history
        )
        self.tee_output_tracer = OutputRecorder.instance()
        self.registered_tracers: List[Type[pyc.BaseTracer]] = [
            OutputRecorder,
            DataflowTracer,
        ]
        self.tracer_cleanup_callbacks: List[Callable] = []
        self.tracer_cleanup_pending: bool = False
        self.syntax_transforms_enabled: bool = True
        self.syntax_transforms_only: bool = False
        self._saved_meta_path_entries: List[TraceFinder] = []

    def make_cell_magic(self, cell_magic_name, run_cell_func=None):
        if run_cell_func is None:
            # this is to avoid capturing `self` and creating an extra reference to the singleton
            store_history = self.settings.store_history

            def run_cell_func(cell):
                run_cell(cell, store_history=store_history)

        def cell_magic_func(_, cell: str):
            asyncio.get_event_loop().run_until_complete(
                self.pyc_execute(cell, False, run_cell_func)
            )

        # FIXME (smacke): probably not a great idea to rely on this
        cell_magic_func.__name__ = cell_magic_name
        return register_cell_magic(cell_magic_func)

    def cleanup_tracers(self):
        self._restore_meta_path()
        for cleanup in reversed(self.tracer_cleanup_callbacks):
            cleanup()
        self.tracer_cleanup_callbacks.clear()
        self.tracer_cleanup_pending = False

    def cell_counter(self):
        return singletons.flow().cell_counter()

    @contextmanager
    def _patch_tracer_filters(
        self,
        tracer: pyc.BaseTracer,
    ) -> Generator[None, None, None]:
        orig_passes_filter = tracer.__class__.file_passes_filter_for_event
        orig_checker = tracer.__class__.should_instrument_file
        try:
            if not isinstance(tracer, (ModuleIniter, StackFrameManager)) or isinstance(
                tracer, DataflowTracer
            ):
                tracer.__class__.file_passes_filter_for_event = (
                    lambda *args: tracer.__class__ in self.registered_tracers
                    and orig_passes_filter(*args)
                )
            tracer.__class__.should_instrument_file = lambda *_: False
            yield
        finally:
            tracer.__class__.file_passes_filter_for_event = orig_passes_filter
            tracer.__class__.should_instrument_file = orig_checker

    @contextmanager
    def _patch_pyccolo_exec_eval(self):
        """
        The purpose of this context manager is to disable this project's
        tracer inside pyccolo's "exec()" functions, since it probably
        will not work properly inside of these.
        """
        orig_exec = pyc.exec
        orig_eval = pyc.eval
        orig_tracer_exec = pyc.BaseTracer.exec
        orig_tracer_eval = pyc.BaseTracer.eval

        def _patched_exec(*args, **kwargs):
            with DataflowTracer.instance().tracing_disabled():
                return orig_exec(
                    *args,
                    num_extra_lookback_frames=kwargs.pop("num_extra_lookback_frames", 0)
                    + 1,
                    **kwargs,
                )

        def _patched_eval(*args, **kwargs):
            with DataflowTracer.instance().tracing_disabled():
                return orig_eval(
                    *args,
                    num_extra_lookback_frames=kwargs.pop("num_extra_lookback_frames", 0)
                    + 1,
                    **kwargs,
                )

        def _patched_tracer_exec(*args, **kwargs):
            with DataflowTracer.instance().tracing_disabled():
                return orig_tracer_exec(
                    *args,
                    num_extra_lookback_frames=kwargs.pop("num_extra_lookback_frames", 0)
                    + 1,
                    **kwargs,
                )

        def _patched_tracer_eval(*args, **kwargs):
            with DataflowTracer.instance().tracing_disabled():
                return orig_tracer_eval(
                    *args,
                    num_extra_lookback_frames=kwargs.pop("num_extra_lookback_frames", 0)
                    + 1,
                    **kwargs,
                )

        try:
            pyc.exec = _patched_exec
            pyc.eval = _patched_eval
            pyc.BaseTracer.exec = _patched_tracer_exec
            pyc.BaseTracer.eval = _patched_tracer_eval
            yield
        finally:
            pyc.exec = orig_exec
            pyc.eval = orig_eval
            pyc.BaseTracer.exec = orig_tracer_exec
            pyc.BaseTracer.eval = orig_tracer_eval

    def make_rewriter_and_syntax_augmenters(
        self,
        tracers: Optional[List[pyc.BaseTracer]] = None,
        ast_rewriter: Optional[pyc.AstRewriter] = None,
    ) -> Tuple[Optional[pyc.AstRewriter], List[Callable]]:
        tracers = (
            [tracer.instance() for tracer in self.registered_tracers]
            if tracers is None
            else tracers
        )
        if len(tracers) == 0:
            return None, []
        ast_rewriter = ast_rewriter or DataflowAstRewriter(tracers)
        # ast_rewriter = ast_rewriter or tracers[-1].make_ast_rewriter()
        all_syntax_augmenters = []
        for tracer in tracers:
            all_syntax_augmenters.extend(tracer.make_syntax_augmenters(ast_rewriter))
        return ast_rewriter, all_syntax_augmenters

    @contextmanager
    def _syntax_transform_only_tracing_context(
        self, syntax_transforms_enabled: bool, all_tracers, ast_rewriter=None
    ):
        if syntax_transforms_enabled:
            ast_rewriter = ast_rewriter or DataflowTracer.instance().make_ast_rewriter(
                module_id=self.cell_counter()
            )
            _, all_syntax_augmenters = self.make_rewriter_and_syntax_augmenters(
                tracers=all_tracers, ast_rewriter=ast_rewriter
            )
        else:
            all_syntax_augmenters = []
        with input_transformer_context(all_syntax_augmenters):
            yield

    def _restore_meta_path(self) -> None:
        while self._saved_meta_path_entries:
            sys.meta_path.insert(0, self._saved_meta_path_entries.pop())

    @contextmanager
    def _tracing_context(
        self, syntax_transforms_enabled: bool, should_capture_output: bool
    ):
        self.before_enter_tracing_context()

        try:
            all_tracers = [
                tracer.instance()
                for tracer in self.registered_tracers
                if tracer is not OutputRecorder or should_capture_output
            ]
            if self.syntax_transforms_only:
                with self._syntax_transform_only_tracing_context(
                    syntax_transforms_enabled, all_tracers
                ):
                    yield
                return
            else:
                self._restore_meta_path()
            if any(tracer.has_sys_trace_events for tracer in all_tracers):
                if not any(
                    isinstance(tracer, StackFrameManager) for tracer in all_tracers
                ):
                    # TODO: decouple this from the dataflow tracer
                    StackFrameManager.clear_instance()
                    all_tracers.append(StackFrameManager.instance())
            all_tracers.insert(0, ModuleIniter.instance())
            for tracer in all_tracers:
                tracer.reset()
            if DataflowTracer.instance() in all_tracers:
                DataflowTracer.instance().init_symtab()
            with pyc.multi_context(
                [self._patch_tracer_filters(tracer) for tracer in all_tracers]
            ):
                if len(self.tracer_cleanup_callbacks) == 0:
                    for idx, tracer in enumerate(all_tracers):
                        self.tracer_cleanup_callbacks.append(
                            tracer.tracing_non_context(
                                do_patch_meta_path=idx == len(all_tracers) - 1
                            )
                        )
                else:
                    for tracer in all_tracers:
                        tracer._enable_tracing(check_disabled=False)
                ast_rewriter = DataflowTracer.instance().make_ast_rewriter(
                    module_id=self.cell_counter()
                )
                with self._syntax_transform_only_tracing_context(
                    syntax_transforms_enabled, all_tracers, ast_rewriter=ast_rewriter
                ):
                    with ast_transformer_context([ast_rewriter]):
                        with self._patch_pyccolo_exec_eval():
                            with self.inner_tracing_context():
                                yield
                if DataflowTracer.instance() in all_tracers:
                    DataflowTracer.instance().finish_cell_hook()
                if self.tracer_cleanup_pending:
                    self.cleanup_tracers()
                else:
                    for tracer in reversed(all_tracers):
                        tracer._disable_tracing(check_enabled=False)
                    # remove pyccolo meta path entries when not executing as they seem to
                    # mess up completions
                    while isinstance(sys.meta_path[0], TraceFinder):
                        self._saved_meta_path_entries.append(sys.meta_path.pop(0))
        except Exception:
            logger.exception("encountered an exception")
            raise

    async def pyc_execute(self, cell_content: str, is_async: bool, run_cell_func):
        with save_number_of_currently_executing_cell():
            return await self._pyc_execute_impl(cell_content, is_async, run_cell_func)

    async def _pyc_execute_impl(self, cell_content: str, is_async: bool, run_cell_func):
        ret = None
        # Stage 1: Run pre-execute hook
        maybe_new_content = self.before_execute(cell_content)
        if maybe_new_content is not None:
            cell_content = maybe_new_content

        # Stage 2: Trace / run the cell, updating dependencies as they are encountered.
        should_trace = self.should_trace()
        is_already_recording_output = cell_content.strip().startswith("%%capture")
        should_capture_output = should_trace and not is_already_recording_output
        output_captured = False
        try:
            with self._tracing_context(
                self.syntax_transforms_enabled
                # disable syntax transforms for cell magics
                and not cell_content.strip().startswith("%%"),
                should_capture_output,
            ) if should_trace else suppress():
                if is_async:
                    ret = await run_cell_func(cell_content)  # pragma: no cover
                else:
                    ret = run_cell_func(cell_content)
                if is_already_recording_output:
                    outvar = (
                        cell_content.strip().splitlines()[0][len("%%capture") :].strip()
                    )
                    # TODO: add all live refs as dependencies
                    singletons.flow().global_scope.upsert_data_symbol_for_name(
                        outvar, get_ipython().user_ns.get(outvar)
                    )
            # Stage 3:  Run post-execute hook
            self.after_execute(cell_content)
            if should_capture_output:
                self.tee_output_tracer.capture_output_tee.__exit__(None, None, None)
                output_captured = True
        except Exception as e:
            if should_capture_output and not output_captured:
                self.tee_output_tracer.capture_output_tee.__exit__(None, None, None)
            logger.exception("exception occurred")
            self.on_exception(e)
        else:
            self.on_exception(None)
        return ret

    @classmethod
    def make_zmq_kernel_class(cls, name: str) -> Type[IPythonKernel]:
        class ZMQKernel(cls, IPythonKernel):  # type: ignore
            implementation = "kernel"
            implementation_version = __version__
            prev_kernel_class: Optional[Type[IPythonKernel]] = None
            replacement_class: Optional[Type[IPythonKernel]] = None
            client_comm: Optional["Comm"] = None

            def __init__(self, **kwargs) -> None:
                super().__init__(**kwargs)
                # this needs to happen after IPythonKernel.__init__ completes,
                # which means that we cannot put it in _initialize(), since at
                # that point only PyccoloKernelHooks.__init__ will be finished
                self.after_init_class()

            def _initialize(self, **kwargs) -> None:
                super()._initialize(**kwargs)
                from ipyflow.kernel import patched_nest_asyncio

                patched_nest_asyncio.apply()

                # As of 2023/05/21, it seems like this is only necessary in
                # the server extension, but seems like it can't hurt to do
                # it here as well.
                patch_jupyter_taskrunner_run()

            @classmethod
            def inject(
                zmq_kernel_class, prev_kernel_class: Type[IPythonKernel]
            ) -> None:
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

            def init_metadata(self, parent):
                """
                Don't actually change the metadata; we just want to get the cell id
                out of the execution request.
                """
                self.before_init_metadata(parent)
                return super().init_metadata(parent)

            def _is_code_empty(self, code: str) -> bool:
                return (
                    self.shell.input_transformer_manager.transform_cell(code).strip()
                    == ""
                )

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

                    if silent or not store_history or self._is_code_empty(code):
                        # then it's probably a control message; don't run through ipyflow
                        ret = await _run_cell_func(code)
                    else:
                        ret = await self.pyc_execute(code, True, _run_cell_func)
                    if ret["status"] == "error":
                        self.on_exception(ret["ename"])
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

                    ret = asyncio.get_event_loop().run_until_complete(
                        _run_cell_func(code)
                        if silent or not store_history or self._is_code_empty(code)
                        else self.pyc_execute(code, True, _run_cell_func)
                    )
                    if ret["status"] == "error":
                        self.on_exception(ret["ename"])
                    self._maybe_eject()
                    return ret

        ZMQKernel.__name__ = name
        return ZMQKernel


class IPyflowKernelBase(singletons.IPyflowKernel, PyccoloKernelMixin):
    def after_init_class(self) -> None:
        NotebookFlow.instance(use_comm=True)

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

    def before_enter_tracing_context(self) -> None:
        flow_ = singletons.flow()
        flow_.updated_symbols.clear()

    @contextmanager
    def inner_tracing_context(self) -> Generator[None, None, None]:
        singletons.flow().init_virtual_symbols()
        with singletons.tracer().dataflow_tracing_disabled_patch(
            get_ipython(),
            "run_line_magic",
            kwarg_transforms={"_stack_depth": (1, lambda d: d + 1)},
        ):
            with singletons.tracer().dataflow_tracing_disabled_patch(
                get_ipython(), "run_cell_magic"
            ):
                yield

    def should_trace(self) -> bool:
        return singletons.flow().mut_settings.dataflow_enabled

    def before_execute(self, cell_content: str) -> Optional[str]:
        flow_ = singletons.flow()
        if (
            -1 < flow_._reactivity_toggled_timestamp < flow_.cell_counter()
            and flow_.mut_settings.exec_mode == ExecutionMode.NORMAL
        ):
            flow_.toggle_reactivity()
            flow_._reactivity_toggled_timestamp = -1
        self.syntax_transforms_enabled = flow_.mut_settings.syntax_transforms_enabled
        self.syntax_transforms_only = flow_.mut_settings.syntax_transforms_only
        flow_.test_and_clear_waiter_usage_detected()
        flow_.test_and_clear_out_of_order_usage_detected_counter()
        if flow_._saved_debug_message is not None:  # pragma: no cover
            logger.error(flow_._saved_debug_message)
            flow_._saved_debug_message = None

        cell_id, flow_._active_cell_id = flow_._active_cell_id, None
        placeholder_id = cell_id is None
        if placeholder_id:
            cell_id = flow_.cell_counter()
        cell = CodeCell.create_and_track(
            cell_id,
            cell_content,
            flow_._tags,
            validate_ipython_counter=self.settings.store_history,
            placeholder_id=placeholder_id,
        )

        last_content, flow_.last_executed_content = (
            flow_.last_executed_content,
            cell_content,
        )
        last_cell_id, flow_.last_executed_cell_id = flow_.last_executed_cell_id, cell_id

        if not flow_.mut_settings.dataflow_enabled:
            return None

        # Stage 1: Precheck.
        if DataflowTracer in self.registered_tracers:
            try:
                flow_._safety_precheck_cell(cell)
            except Exception:
                logger.exception("exception occurred during precheck")

            used_out_of_order_counter = flow_.out_of_order_usage_detected_counter
            if (
                flow_.mut_settings.warn_out_of_order_usages
                and used_out_of_order_counter is not None
                and (cell_id, cell_content)
                != (
                    last_cell_id,
                    last_content,
                )
            ):
                logger.warning(
                    "detected out of order usage of cell [%d]; showing previous output if any (run again to ignore force execution)",
                    used_out_of_order_counter,
                )
                return "pass"
        return None

    def _handle_output(self) -> None:
        flow_ = singletons.flow()
        prev_cell = None
        cell = CodeCell.current_cell()
        if len(cell.history) >= 2:
            prev_cell = CodeCell.at_timestamp(cell.history[-2])
        if (
            flow_.mut_settings.warn_out_of_order_usages
            and flow_.out_of_order_usage_detected_counter is not None
            and prev_cell is not None
            and prev_cell.captured_output is not None
        ):
            prev_cell.captured_output.show()
        if prev_cell is not None:
            captured = prev_cell.captured_output
            if captured is not None and (
                sum(
                    sum(len(datum) for datum in output.data.values())
                    for output in captured.outputs
                )
                + len(captured.stdout)
                + len(captured.stderr)
                > 256
            ):
                # don't save potentially large outputs for previous versions
                prev_cell.captured_output = None
        cell.captured_output = self.tee_output_tracer.capture_output

    def after_execute(self, cell_content: str) -> None:
        self._handle_output()
        # resync any defined symbols that could have gotten out-of-sync
        # due to tracing being disabled
        flow_ = singletons.flow()
        if not flow_.mut_settings.dataflow_enabled:
            return
        flow_._resync_symbols(
            [
                # TODO: avoid bad performance by only iterating over symbols updated in this cell
                sym
                for sym in flow_.all_data_symbols()
                if sym.timestamp.cell_num == CodeCell.exec_counter()
            ]
        )
        flow_._add_applicable_prev_cell_parents_to_current()
        flow_.gc()

    def on_exception(self, e: Union[None, str, Exception]) -> None:
        singletons.flow().get_and_set_exception_raised_during_execution(e)


IPyflowKernel = IPyflowKernelBase.make_zmq_kernel_class("IPyflowKernel")
