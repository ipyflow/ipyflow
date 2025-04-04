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
from ipyflow.data_model.statement import Statement
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.flow import NotebookFlow
from ipyflow.memoization import MemoizedOutputLevel
from ipyflow.tracing.flow_ast_rewriter import DataflowAstRewriter
from ipyflow.tracing.interrupt_tracer import InterruptTracer
from ipyflow.tracing.ipyflow_tracer import DataflowTracer, StackFrameManager
from ipyflow.tracing.output_recorder import OutputRecorder
from ipyflow.utils.ipython_utils import (
    ast_transformer_context,
    input_transformer_context,
    make_mro_inserter_metaclass,
    print_purple,
    save_number_of_currently_executing_cell,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


_CAPTURE_OUTPUT_SAVE_LIMIT = 2 * 1024 * 1024


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
            InterruptTracer,
        ]
        self.tracer_cleanup_callbacks: List[Callable] = []
        self.tracer_cleanup_pending: bool = False
        self.syntax_transforms_enabled: bool = True
        self.syntax_transforms_only: bool = False
        self._saved_meta_path_entries: List[TraceFinder] = []
        self._has_cell_id: bool = (
            "cell_id" in inspect.signature(super()._run_cell).parameters
        )
        self._should_capture_output = False

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
        if ipy.displayhook.exec_result is None:
            # we are not currently running a cell, so the cell counter will be too high
            Cell._cell_counter -= 1
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

    def make_ipython_cell_name(self) -> str:
        cur_cell = Cell.current_cell()
        cell_name = cur_cell.make_ipython_name()
        return cell_name

    @contextmanager
    def _patch_tracer_filters(
        self,
        tracer: pyc.BaseTracer,
    ) -> Generator[None, None, None]:
        orig_passes_filter = tracer.__class__.file_passes_filter_for_event
        orig_checker = tracer.__class__.should_instrument_file
        try:
            if not isinstance(tracer, StackFrameManager) or isinstance(
                tracer, DataflowTracer
            ):
                tracer.__class__.file_passes_filter_for_event = (  # type: ignore[method-assign]
                    lambda *args: tracer.__class__ in self.registered_tracers
                    and orig_passes_filter(*args)
                )
            tracer.__class__.should_instrument_file = (  # type: ignore[method-assign]
                lambda *args: tracer.__class__ in self.registered_tracers
                and orig_checker(*args)
            )
            yield
        finally:
            tracer.__class__.file_passes_filter_for_event = orig_passes_filter  # type: ignore[method-assign]
            tracer.__class__.should_instrument_file = orig_checker  # type: ignore[method-assign]

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
        path: Optional[str] = None,
    ) -> Tuple[Optional[pyc.AstRewriter], List[Callable]]:
        tracers = (
            [tracer.instance() for tracer in self.registered_tracers]
            if tracers is None
            else tracers
        )
        if len(tracers) == 0:
            return None, []
        ast_rewriter = ast_rewriter or DataflowAstRewriter(tracers, path=path)  # type: ignore[arg-type]
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
                self.make_ipython_cell_name(), module_id=self.cell_counter()
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
    def _tracing_context(self, syntax_transforms_enabled: bool):
        self.before_enter_tracing_context()

        try:
            all_tracers = [
                tracer.instance()
                for tracer in self.registered_tracers
                if tracer is not OutputRecorder or self._should_capture_output
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
            cell_name = self.make_ipython_cell_name()
            singletons.flow().set_name_to_cell_num_mapping(
                cell_name, self.execution_count
            )
            for tracer in all_tracers:
                tracer._tracing_enabled_files.add(cell_name)
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
                    cell_name, module_id=self.cell_counter()
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
                    while len(sys.meta_path) > 0 and isinstance(
                        sys.meta_path[0], TraceFinder
                    ):
                        self._saved_meta_path_entries.append(sys.meta_path[0])
                        sys.meta_path.pop(0)
        except Exception:
            logger.exception("encountered an exception")
            raise

    def _is_code_empty(self, code: str) -> bool:
        return self.input_transformer_manager.transform_cell(code).strip() == ""

    def _reset_should_capture_output(self) -> bool:
        ret = self._should_capture_output
        self._should_capture_output = False
        return ret

    def run_cell(
        self,
        raw_cell,
        store_history=False,
        silent=False,
        shell_futures=True,
        cell_id=None,
        **kwargs,
    ):
        if self._has_cell_id:
            kwargs["cell_id"] = cell_id
        # save it off in case extension is disabled
        tee_output_tracer = self.tee_output_tracer
        try:
            return super().run_cell(
                raw_cell,
                store_history=store_history,
                silent=silent,
                shell_futures=shell_futures,
                **kwargs,
            )
        finally:
            # won't be available if extension is disabled
            getattr(self, "_reset_should_capture_output", lambda: None)()
            # Kind of weird -- we enter the context using the tracer to ensure it only picks up
            # user output, but we don't exit it until here to ensure we also pick up output from
            # ipython post execute hooks (e.g. where matplotlib flushes buffers).
            tee_output_tracer.done_capturing_output()

    async def run_cell_async(
        self,
        raw_cell: str,
        store_history=False,
        silent=False,
        shell_futures=True,
        cell_id=None,
        **kwargs,
    ) -> ExecutionResult:
        if self._has_cell_id:
            kwargs["cell_id"] = cell_id
        if silent or self._is_code_empty(raw_cell):
            # then it's probably a control message; don't run through ipyflow
            ret = await super().run_cell_async(
                raw_cell,
                store_history=store_history,
                silent=silent,
                shell_futures=shell_futures,
                **kwargs,
            )
        else:
            with save_number_of_currently_executing_cell():
                ret = await self._ipyflow_run_cell(
                    raw_cell,
                    store_history=store_history,
                    shell_futures=shell_futures,
                    **kwargs,
                )
        self._maybe_eject()
        return ret

    async def _ipyflow_run_cell(
        self,
        raw_cell: str,
        store_history=False,
        shell_futures=True,
        **kwargs,
    ) -> ExecutionResult:
        # Stage 1: Run pre-execute hook
        maybe_new_content = self.before_run_cell(
            raw_cell, store_history=store_history, **kwargs
        )
        if maybe_new_content is not None:
            raw_cell = maybe_new_content
        # Stage 2: Trace / run the cell, updating dependencies as they are encountered.
        settings = singletons.flow().mut_settings
        should_trace = settings.dataflow_enabled
        is_already_recording_output = raw_cell.strip().startswith("%%capture")
        self._should_capture_output = should_trace and not is_already_recording_output
        try:
            with (
                self._tracing_context(
                    self.syntax_transforms_enabled
                    # disable syntax transforms for cell magics
                    and not raw_cell.strip().startswith("%%"),
                )
                if should_trace
                else suppress()
            ):
                has_transformed_cell = kwargs.pop("transformed_cell", None) is not None
                try:
                    transformed_cell = self.transform_cell(raw_cell)
                except Exception:
                    transformed_cell = raw_cell
                if has_transformed_cell:
                    kwargs["transformed_cell"] = transformed_cell
                # discard any previous transformations that were done
                cell = Cell.current_cell()
                ret = await super().run_cell_async(
                    cell.raw_cell if has_transformed_cell else transformed_cell,
                    store_history=store_history,
                    silent=False,
                    shell_futures=shell_futures,
                    **kwargs,
                )  # pragma: no cover
                cell.error_in_exec = ret.error_in_exec
                if is_already_recording_output:
                    outvar = (
                        raw_cell.strip().splitlines()[0][len("%%capture") :].strip()
                    )
                    # TODO: add all live refs as dependencies
                    singletons.flow().global_scope.upsert_symbol_for_name(
                        outvar, get_ipython().user_ns.get(outvar)
                    )
            # Stage 3:  Run post-execute hook
            if should_trace:
                self.after_run_cell(raw_cell)
            elif cell.prev_cell is not None:
                cell.raw_static_parents = cell.prev_cell.raw_static_parents
                cell.raw_dynamic_parents = cell.prev_cell.raw_dynamic_parents
        except Exception as e:
            if settings.is_dev_mode:
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
            InteractiveShell,
            "run_line_magic",  # type: ignore
            kwarg_transforms={"_stack_depth": (1, lambda d: d + 1)},
        ):
            with singletons.tracer().dataflow_tracing_disabled_patch(
                InteractiveShell, "run_cell_magic"
            ):
                yield

    def _get_content_for_memoized_run(self, cell: Cell) -> Optional[str]:
        prev_cell = cell.prev_cell
        if prev_cell is None:
            return None
        identical_result_ctr = cell.get_memoized_counter()
        if identical_result_ctr is None:
            return None
        (
            _,
            memoized_outputs,
            memoized_display_output,
            _,
        ) = prev_cell._memoized_executions[cell.executed_content or ""][
            identical_result_ctr
        ]
        assert memoized_outputs is not None
        assert memoized_display_output is not None

        # TODO: split this method up here

        for idx, stmt_node in enumerate(cell.to_ast().body):
            Statement.create_and_track(
                stmt_node, timestamp=Timestamp(self.cell_counter(), idx)
            )

        cell.skipped_due_to_memoization_ctr = identical_result_ctr
        print_purple(
            "Detected identical symbol usages to previous run; reusing memoized result..."
        )
        for sym, out_ts, value in memoized_outputs:
            if sym.obj is not value:
                self.user_ns[sym.name] = value
                sym.update_obj_ref(value)
            new_updated_ts = Timestamp(self.cell_counter(), out_ts.stmt_num)
            sym.refresh(timestamp=new_updated_ts)
        if cell.memoized_output_level == MemoizedOutputLevel.VERBOSE:
            cell.captured_output = memoized_display_output
            memoized_display_output.show(render_out_expr=False)
        return cell.get_transformed_memoized_content(ctr=identical_result_ctr)

    def before_run_cell(
        self,
        cell_content: str,
        store_history: bool,
        cell_id: Optional[str] = None,
        **_kwargs,
    ) -> Optional[str]:
        original_content = cell_content
        (
            new_cell_content,
            memoized_output_level,
        ) = Cell.get_memoized_content_and_output_level(cell_content)
        if new_cell_content is not None:
            cell_content = new_cell_content
        flow_ = singletons.flow()
        settings = flow_.mut_settings
        if (
            settings.interface == Interface.UNKNOWN
            and getattr(self, "kernel", None) is None
        ):
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
        if to_create_cell_id is None:
            # next counter because it gets bumped on creation
            to_create_cell_id = Cell.next_exec_counter()
        cell = Cell.create_and_track(
            to_create_cell_id,
            original_content,
            flow_._tags,
            validate_ipython_counter=store_history,
            placeholder_id=placeholder_id,
            memoized_output_level=memoized_output_level,
        )
        cell.executed_content = cell_content

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

        memoized_run_content = self._get_content_for_memoized_run(cell)
        if memoized_run_content is not None:
            return memoized_run_content

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
                cell.executed_content = None
                if cell.prev_cell is None:
                    return "pass"
                else:
                    return f"Out.get({cell.prev_cell.cell_ctr})"
        return cell_content

    def _handle_memoization(self) -> None:
        cell = Cell.current_cell()
        prev_cell = cell.prev_cell
        if cell.skipped_due_to_memoization_ctr > 0:
            assert prev_cell is not None
            cell.to_ast(override=prev_cell.to_ast())
            prev_cell = Cell.at_counter(cell.skipped_due_to_memoization_ctr)
            assert prev_cell is not None
            for _ in singletons.flow().mut_settings.iter_slicing_contexts():
                for parent, syms in list(cell.raw_parents.items()):
                    cell.remove_parent_edges(parent, syms)
                for parent, syms in prev_cell.raw_parents.items():
                    cell.add_parent_edges(parent, syms)
                for stmt, prev_stmt in zip(cell.statements(), prev_cell.statements()):
                    for parent, syms in list(stmt.raw_parents.items()):
                        stmt.remove_parent_edges(parent, syms)
                    for parent, syms in prev_stmt.raw_parents.items():
                        stmt.add_parent_edges(parent, syms)
        elif cell.is_memoized:
            cell._maybe_memoize_params()

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
                > _CAPTURE_OUTPUT_SAVE_LIMIT
            ):
                # don't save potentially large outputs for previous versions
                prev_cell.captured_output = None
        if cell.captured_output is None:
            cell.captured_output = self.tee_output_tracer.capture_output

    def after_run_cell(self, _cell_content: str) -> None:
        self._handle_output()
        # resync any defined symbols that could have gotten out-of-sync
        # due to tracing being disabled
        flow_ = singletons.flow()
        if not flow_.mut_settings.dataflow_enabled:
            return
        # TODO: avoid bad performance by keeping track of symbols updated in this cell
        this_cell_symbols = [
            sym
            for sym in flow_.all_symbols()
            if sym.timestamp.cell_num == Cell.exec_counter()
        ]
        this_cell_dangling_symbols = {
            sym for sym in this_cell_symbols if sym._is_dangling_on_edges
        }
        for sym in this_cell_dangling_symbols:
            sym._is_dangling_on_edges = False
        flow_._resync_symbols(this_cell_symbols)
        self._handle_memoization()
        flow_._remove_dangling_parent_edges(this_cell_dangling_symbols)
        flow_.gc()

    def on_exception(self, e: Union[None, str, Exception]) -> None:
        singletons.flow().get_and_set_exception_raised_during_execution(e)


UsesIPyflowShell = make_mro_inserter_metaclass(
    InteractiveShell, IPyflowInteractiveShell
)
