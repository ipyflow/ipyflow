# -*- coding: utf-8 -*-
import asyncio
import inspect
import logging
from contextlib import contextmanager, suppress
from typing import Callable, List, Generator, NamedTuple, Optional, Tuple, Type

import pyccolo as pyc
from ipykernel.ipkernel import IPythonKernel
from IPython.core.magic import register_cell_magic

from ipyflow import singletons
from ipyflow.data_model.code_cell import cells
from ipyflow.ipython_utils import (
    ast_transformer_context,
    capture_output_tee,
    input_transformer_context,
    run_cell,
    save_number_of_currently_executing_cell,
)
from ipyflow.flow import NotebookFlow
from ipyflow.tracing.ipyflow_tracer import (
    ModuleIniter,
    DataflowTracer,
    StackFrameManager,
)
from ipyflow.tracing.flow_ast_rewriter import DataflowAstRewriter
from ipyflow.version import __version__


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class PyccoloKernelSettings(NamedTuple):
    store_history: bool


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

    def after_execute(self, cell_content: str) -> None:
        ...

    def on_exception(self, e: Exception) -> None:
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


class PyccoloKernelMixin(PyccoloKernelHooks):
    def __init__(self, **kwargs):
        self.settings: PyccoloKernelSettings = PyccoloKernelSettings(
            store_history=kwargs.pop("store_history", True)
        )
        super().__init__(**kwargs)

        self.tee_output_tracer = OutputRecorder.instance()
        self.registered_tracers: List[Type[pyc.BaseTracer]] = [
            OutputRecorder,
            DataflowTracer,
        ]
        self.tracer_cleanup_callbacks: List[Callable] = []
        self.tracer_cleanup_pending: bool = False
        self.syntax_transforms_only: bool = False

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
    def _syntax_transform_only_tracing_context(self, all_tracers, ast_rewriter=None):
        ast_rewriter = ast_rewriter or DataflowTracer.instance().make_ast_rewriter(
            module_id=self.cell_counter()
        )
        _, all_syntax_augmenters = self.make_rewriter_and_syntax_augmenters(
            tracers=all_tracers, ast_rewriter=ast_rewriter
        )
        with input_transformer_context(all_syntax_augmenters):
            yield

    @contextmanager
    def _tracing_context(self):
        self.before_enter_tracing_context()

        try:
            all_tracers = [tracer.instance() for tracer in self.registered_tracers]
            if self.syntax_transforms_only:
                with self._syntax_transform_only_tracing_context(all_tracers):
                    yield
                return
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
            with pyc.multi_context(
                [self._patch_tracer_filters(tracer) for tracer in all_tracers]
            ):
                if len(self.tracer_cleanup_callbacks) == 0:
                    for tracer in all_tracers:
                        self.tracer_cleanup_callbacks.append(
                            tracer.tracing_non_context()
                        )
                else:
                    for tracer in all_tracers:
                        tracer._enable_tracing(check_disabled=False)
                ast_rewriter = DataflowTracer.instance().make_ast_rewriter(
                    module_id=self.cell_counter()
                )
                with self._syntax_transform_only_tracing_context(
                    all_tracers, ast_rewriter=ast_rewriter
                ):
                    with ast_transformer_context([ast_rewriter]):
                        with self._patch_pyccolo_exec_eval():
                            yield
                if self.tracer_cleanup_pending:
                    self.cleanup_tracers()
                else:
                    for tracer in reversed(all_tracers):
                        tracer._disable_tracing(check_enabled=False)
        except Exception:
            logger.exception("encountered an exception")
            raise

    async def pyc_execute(self, cell_content: str, is_async: bool, run_cell_func):
        ret = None
        with save_number_of_currently_executing_cell():
            # Stage 1: Run pre-execute hook
            maybe_new_content = self.before_execute(cell_content)
            if maybe_new_content is not None:
                cell_content = maybe_new_content

            # Stage 2: Trace / run the cell, updating dependencies as they are encountered.
            try:
                with self._tracing_context() if self.should_trace() else suppress():
                    if is_async:
                        ret = await run_cell_func(cell_content)  # pragma: no cover
                    else:
                        ret = run_cell_func(cell_content)
                # Stage 3:  Run post-execute hook
                self.after_execute(cell_content)
            except Exception as e:
                self.tee_output_tracer.capture_output_tee.__exit__(None, None, None)
                logger.exception("exception occurred")
                self.on_exception(e)
            finally:
                self.tee_output_tracer.capture_output_tee.__exit__(None, None, None)
                return ret

    @classmethod
    def make_zmq_kernel_class(cls, name: str) -> Type[IPythonKernel]:
        class ZMQKernel(cls, IPythonKernel):  # type: ignore
            implementation = "kernel"
            implementation_version = __version__

            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                from ipyflow.kernel import patched_nest_asyncio

                patched_nest_asyncio.apply()
                self.after_init_class()

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
                ):
                    super_ = super()

                    async def _run_cell_func(cell):
                        return await super_.do_execute(
                            cell, silent, store_history, user_expressions, allow_stdin
                        )

                    if silent:
                        # then it's probably a control message; don't run through ipyflow
                        return await _run_cell_func(code)
                    else:
                        return await self.pyc_execute(code, True, _run_cell_func)

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
                        _run_cell_func(code)
                        if silent
                        else self.pyc_execute(code, True, _run_cell_func)
                    )

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
        flow_.updated_reactive_symbols.clear()
        flow_.updated_deep_reactive_symbols.clear()
        self.syntax_transforms_only = flow_.mut_settings.syntax_transforms_only

    def should_trace(self) -> bool:
        return singletons.flow().mut_settings.dataflow_enabled

    def before_execute(self, cell_content: str) -> Optional[str]:
        flow_ = singletons.flow()
        flow_.test_and_clear_waiter_usage_detected()
        flow_.test_and_clear_out_of_order_usage_detected_counter()
        if flow_._saved_debug_message is not None:  # pragma: no cover
            logger.error(flow_._saved_debug_message)
            flow_._saved_debug_message = None

        cell_id, flow_._active_cell_id = flow_._active_cell_id, None
        if cell_id is None:
            cell_id = flow_.cell_counter()
        cell = cells().create_and_track(
            cell_id,
            cell_content,
            flow_._tags,
            validate_ipython_counter=self.settings.store_history,
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
            flow_._safety_precheck_cell(cell)

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
        prev_cell = None
        cell = cells().current_cell()
        if len(cell.history) >= 2:
            prev_cell = cells().from_timestamp(cell.history[-2])
        if (
            singletons.flow().out_of_order_usage_detected_counter
            and prev_cell is not None
            and prev_cell.captured_output is not None
        ):
            prev_cell.captured_output.show()
        if prev_cell is not None:
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
                if sym.timestamp.cell_num == cells().exec_counter()
            ]
        )
        flow_.gc()

    def on_exception(self, e: Exception) -> None:
        flow_ = singletons.flow()
        if flow_.is_test:
            flow_.set_exception_raised_during_execution(e)


IPyflowKernel = IPyflowKernelBase.make_zmq_kernel_class("IPyflowKernel")
