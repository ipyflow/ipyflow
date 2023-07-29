# -*- coding: utf-8 -*-
import inspect
import logging
import sys
from contextlib import contextmanager, suppress
from typing import Callable, Generator, List, Optional, Tuple, Type, Union

import pyccolo as pyc
from IPython import get_ipython
from IPython.core.interactiveshell import ExecutionResult, InteractiveShell
from pyccolo.import_hooks import TraceFinder

from ipyflow import singletons
from ipyflow.config import Interface
from ipyflow.data_model.cell import Cell
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
    make_mro_inserter_metaclass,
    save_number_of_currently_executing_cell,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


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


class IPyflowInteractiveShell(singletons.IPyflowShell, InteractiveShell):
    prev_shell_class: Optional[Type[InteractiveShell]] = None
    replacement_class: Optional[Type[InteractiveShell]] = None

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._initialize()

    def _initialize(self) -> None:
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
        self._has_cell_id: bool = (
            "cell_id" in inspect.signature(super()._run_cell).parameters
        )

    @classmethod
    def instance(cls, *args, **kwargs) -> "IPyflowInteractiveShell":
        ret = super().instance(*args, **kwargs)
        NotebookFlow.instance()
        return ret

    @classmethod
    def inject(shell_class, prev_shell_class: Type[InteractiveShell]) -> None:
        ipy = get_ipython()
        ipy.__class__ = shell_class
        if shell_class.prev_shell_class is None:
            ipy._initialize()
            for subclass in singletons.IPyflowShell._walk_mro():
                subclass._instance = ipy
        NotebookFlow.instance()
        Cell._cell_counter = ipy.execution_count
        shell_class.prev_shell_class = prev_shell_class

    @classmethod
    def _maybe_eject(shell_class) -> None:
        if shell_class.replacement_class is None:
            return
        get_ipython().__class__ = shell_class.replacement_class
        shell_class.replacement_class = None

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

    def _is_code_empty(self, code: str) -> bool:
        return self.input_transformer_manager.transform_cell(code).strip() == ""

    def _run_cell(
        self,
        raw_cell: str,
        store_history=False,
        silent=False,
        shell_futures=True,
        cell_id=None,
    ) -> ExecutionResult:
        kwargs = {}
        if self._has_cell_id:
            kwargs["cell_id"] = cell_id
        if silent or self._is_code_empty(raw_cell):
            # then it's probably a control message; don't run through ipyflow
            ret = super()._run_cell(
                raw_cell,
                store_history=store_history,
                silent=silent,
                shell_futures=shell_futures,
                **kwargs,
            )
        else:
            with save_number_of_currently_executing_cell():
                ret = self._ipyflow_run_cell(
                    raw_cell,
                    store_history=store_history,
                    silent=silent,
                    shell_futures=shell_futures,
                    **kwargs,
                )
        self._maybe_eject()
        return ret

    def _ipyflow_run_cell(
        self,
        raw_cell: str,
        store_history=False,
        silent=False,
        shell_futures=True,
        **kwargs,
    ) -> ExecutionResult:
        ret = None
        # Stage 1: Run pre-execute hook
        maybe_new_content = self.before_run_cell(
            raw_cell, store_history=store_history, **kwargs
        )
        if maybe_new_content is not None:
            raw_cell = maybe_new_content

        # Stage 2: Trace / run the cell, updating dependencies as they are encountered.
        should_trace = self.should_trace()
        is_already_recording_output = raw_cell.strip().startswith("%%capture")
        should_capture_output = should_trace and not is_already_recording_output
        output_captured = False
        try:
            with self._tracing_context(
                self.syntax_transforms_enabled
                # disable syntax transforms for cell magics
                and not raw_cell.strip().startswith("%%"),
                should_capture_output,
            ) if should_trace else suppress():
                ret = super()._run_cell(
                    raw_cell,
                    store_history=store_history,
                    silent=silent,
                    shell_futures=shell_futures,
                    **kwargs,
                )  # pragma: no cover
                if is_already_recording_output:
                    outvar = (
                        raw_cell.strip().splitlines()[0][len("%%capture") :].strip()
                    )
                    # TODO: add all live refs as dependencies
                    singletons.flow().global_scope.upsert_data_symbol_for_name(
                        outvar, get_ipython().user_ns.get(outvar)
                    )
            # Stage 3:  Run post-execute hook
            self.after_run_cell(raw_cell)
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

    def before_run_cell(
        self, cell_content: str, store_history: bool, cell_id: Optional[str] = None
    ) -> Optional[str]:
        flow_ = singletons.flow()
        settings = flow_.mut_settings
        if settings.interface == Interface.UNKNOWN:
            try:
                singletons.kernel()
            except AssertionError:
                settings.interface = Interface.IPYTHON
        self.syntax_transforms_enabled = settings.syntax_transforms_enabled
        self.syntax_transforms_only = settings.syntax_transforms_only
        flow_.test_and_clear_waiter_usage_detected()
        flow_.test_and_clear_out_of_order_usage_detected_counter()
        if flow_._saved_debug_message is not None:  # pragma: no cover
            logger.error(flow_._saved_debug_message)
            flow_._saved_debug_message = None

        if cell_id is not None:
            flow_.active_cell_id = cell_id
        to_create_cell_id = flow_.active_cell_id
        placeholder_id = to_create_cell_id is None
        if placeholder_id:
            # next counter because it gets bumped on creation
            to_create_cell_id = Cell.next_exec_counter()
        cell = Cell.create_and_track(
            to_create_cell_id,
            cell_content,
            flow_._tags,
            validate_ipython_counter=store_history,
            placeholder_id=placeholder_id,
        )

        last_content, flow_.last_executed_content = (
            flow_.last_executed_content,
            cell_content,
        )
        last_cell_id, flow_.last_executed_cell_id = (
            flow_.last_executed_cell_id,
            to_create_cell_id,
        )

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
                and (to_create_cell_id, cell_content)
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
        cell = Cell.current_cell()
        if len(cell.history) >= 2:
            prev_cell = Cell.at_timestamp(cell.history[-2])
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

    def after_run_cell(self, _cell_content: str) -> None:
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
                if sym.timestamp.cell_num == Cell.exec_counter()
            ]
        )
        flow_._add_applicable_prev_cell_parents_to_current()
        flow_.gc()

    def on_exception(self, e: Union[None, str, Exception]) -> None:
        singletons.flow().get_and_set_exception_raised_during_execution(e)


UsesIPyflowShell = make_mro_inserter_metaclass(
    InteractiveShell, IPyflowInteractiveShell
)
