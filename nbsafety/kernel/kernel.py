# -*- coding: utf-8 -*-
import asyncio
import inspect
import logging
from contextlib import contextmanager
from typing import Callable, List, Generator, NamedTuple, Type

import pyccolo as pyc
from ipykernel.ipkernel import IPythonKernel
from IPython.core.magic import register_cell_magic

from nbsafety import singletons
from nbsafety.data_model.code_cell import cells
from nbsafety.ipython_utils import (
    ast_transformer_context,
    input_transformer_context,
    run_cell,
    save_number_of_currently_executing_cell,
)
from nbsafety.version import __version__
from nbsafety.safety import NotebookSafety
from nbsafety.tracing.nbsafety_tracer import (
    ModuleIniter,
    SafetyTracer,
    StackFrameManager,
)


logger = logging.getLogger(__name__)


class PyccoloKernelSettings(NamedTuple):
    store_history: bool


class SafeKernelHooks:
    def after_init_class(self: "PyccoloKernelMixin") -> None:  # type: ignore
        NotebookSafety.instance(use_comm=True)

    def before_init_metadata(self: "PyccoloKernelMixin", parent) -> None:  # type: ignore
        """
        Don't actually change the metadata; we just want to get the cell id
        out of the execution request.
        """
        nbs_ = singletons.nbs()
        metadata = parent.get("metadata", {})
        cell_id = metadata.get("cellId", None)
        if cell_id is not None:
            nbs_.set_active_cell(cell_id)
        tags = tuple(metadata.get("tags", ()))
        nbs_.set_tags(tags)

    def before_enter_tracing_context(self: "PyccoloKernelMixin"):  # type: ignore
        nbs_ = singletons.nbs()
        nbs_.updated_symbols.clear()
        nbs_.updated_reactive_symbols.clear()
        nbs_.updated_deep_reactive_symbols.clear()

    def before_execute(self: "PyccoloKernelMixin", cell_content: str) -> None:  # type: ignore
        nbs_ = singletons.nbs()
        if nbs_._saved_debug_message is not None:  # pragma: no cover
            logger.error(nbs_._saved_debug_message)
            nbs_._saved_debug_message = None

        cell_id, nbs_._active_cell_id = nbs_._active_cell_id, None
        assert cell_id is not None
        cell = cells().create_and_track(
            cell_id,
            cell_content,
            nbs_._tags,
            validate_ipython_counter=self.settings.store_history,
        )

        # Stage 1: Precheck.
        nbs_._safety_precheck_cell(cell)

    def after_execute(self: "PyccoloKernelMixin", cell_content: str) -> None:  # type: ignore
        # resync any defined symbols that could have gotten out-of-sync
        #  due to tracing being disabled
        nbs_ = singletons.nbs()
        nbs_._resync_symbols(
            [
                # TODO: avoid bad performance by only iterating over symbols updated in this cell
                sym
                for sym in nbs_.all_data_symbols()
                if sym.timestamp.cell_num == cells().exec_counter()
            ]
        )
        nbs_.gc()

    def on_exception(self: "PyccoloKernelMixin", e: Exception) -> None:  # type: ignore
        nbs_ = singletons.nbs()
        if nbs_.is_test:
            nbs_.set_exception_raised_during_execution(e)


class PyccoloKernelMixin(singletons.SafeKernel, SafeKernelHooks):
    def __init__(self, **kwargs):
        self.settings: PyccoloKernelSettings = PyccoloKernelSettings(
            store_history=kwargs.pop("store_history", True)
        )
        super().__init__(**kwargs)

        self.registered_tracers: List[Type[pyc.BaseTracer]] = [SafetyTracer]
        self.tracer_cleanup_callbacks: List[Callable] = []
        self.tracer_cleanup_pending: bool = False
        self.after_init_class()

    def make_cell_magic(self, cell_magic_name, run_cell_func=None):
        # this is to avoid capturing `self` and creating an extra reference to the singleton
        store_history = self.settings.store_history

        if run_cell_func is None:

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
        return singletons.nbs().cell_counter()

    @contextmanager
    def _patch_tracer_filters(
        self,
        tracer: pyc.BaseTracer,
    ) -> Generator[None, None, None]:
        orig_passes_filter = tracer.__class__.file_passes_filter_for_event
        orig_checker = tracer.__class__.should_instrument_file
        try:
            if not isinstance(tracer, (ModuleIniter, StackFrameManager)) or isinstance(
                tracer, SafetyTracer
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
            with SafetyTracer.instance().tracing_disabled():
                return orig_exec(
                    *args,
                    num_extra_lookback_frames=kwargs.pop("num_extra_lookback_frames", 0)
                    + 1,
                    **kwargs,
                )

        def _patched_eval(*args, **kwargs):
            with SafetyTracer.instance().tracing_disabled():
                return orig_eval(
                    *args,
                    num_extra_lookback_frames=kwargs.pop("num_extra_lookback_frames", 0)
                    + 1,
                    **kwargs,
                )

        def _patched_tracer_exec(*args, **kwargs):
            with SafetyTracer.instance().tracing_disabled():
                return orig_tracer_exec(
                    *args,
                    num_extra_lookback_frames=kwargs.pop("num_extra_lookback_frames", 0)
                    + 1,
                    **kwargs,
                )

        def _patched_tracer_eval(*args, **kwargs):
            with SafetyTracer.instance().tracing_disabled():
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

    @contextmanager
    def _tracing_context(self):
        self.before_enter_tracing_context()

        try:
            all_tracers = [tracer.instance() for tracer in self.registered_tracers]
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
                ast_rewriter = SafetyTracer.instance().make_ast_rewriter(
                    module_id=self.cell_counter()
                )
                all_syntax_augmenters = []
                for tracer in all_tracers:
                    all_syntax_augmenters.extend(
                        tracer.make_syntax_augmenters(ast_rewriter)
                    )
                with input_transformer_context(all_syntax_augmenters):
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
            self.before_execute(cell_content)

            # Stage 2: Trace / run the cell, updating dependencies as they are encountered.
            try:
                with self._tracing_context():
                    if is_async:
                        ret = await run_cell_func(cell_content)  # pragma: no cover
                    else:
                        ret = run_cell_func(cell_content)
                # Stage 3:  Run post-execute hook
                self.after_execute(cell_content)
            except Exception as e:
                self.on_exception(e)
            finally:
                return ret


class SafeKernel(PyccoloKernelMixin, IPythonKernel):
    implementation = "kernel"
    implementation_version = __version__

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        import nest_asyncio

        # ref: https://github.com/erdewit/nest_asyncio
        nest_asyncio.apply()

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
                # then it's probably a control message; don't run through nbsafety
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
                self.pyc_execute(code, True, _run_cell_func)
            )
