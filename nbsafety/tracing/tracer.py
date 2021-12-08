# -*- coding: future_annotations -*-
import ast
import builtins
import functools
import logging
import os
import sys
from collections import defaultdict
from contextlib import contextmanager
from typing import TYPE_CHECKING

from traitlets.traitlets import MetaHasTraits

from nbsafety import singletons
from nbsafety.extra_builtins import EMIT_EVENT, TRACING_ENABLED
from nbsafety.tracing.ast_rewriter import AstRewriter
from nbsafety.tracing.import_hooks import TraceFinder
from nbsafety.tracing.syntax_augmentation import AugmentationSpec, make_syntax_augmenter
from nbsafety.tracing.trace_events import TraceEvent
from nbsafety.tracing.trace_stack import TraceStack

if TYPE_CHECKING:
    from typing import Any, Callable, DefaultDict, Dict, FrozenSet, Generator, List, Optional, Set, Tuple, Type, Union
    from types import FrameType


logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


sys_settrace = sys.settrace
internal_directories = (os.path.dirname(os.path.dirname((lambda: 0).__code__.co_filename)),)


class MetaHasTraitsAndTransientState(MetaHasTraits):
    def __call__(cls, *args, **kwargs):
        obj = MetaHasTraits.__call__(cls, *args, **kwargs)
        obj._transient_fields_end()
        return obj


class SingletonTracerStateMachine(singletons.TraceManager, metaclass=MetaHasTraitsAndTransientState):
    ast_rewriter_cls = AstRewriter

    _MANAGER_CLASS_REGISTERED = False
    EVENT_HANDLERS_PENDING_REGISTRATION: DefaultDict[TraceEvent, List[Callable[..., Any]]] = defaultdict(list)
    EVENT_HANDLERS_BY_CLASS: Dict[Type[BaseTracerStateMachine], DefaultDict[TraceEvent, List[Callable[..., Any]]]] = {}

    EVENT_LOGGER = logging.getLogger('events')
    EVENT_LOGGER.setLevel(logging.WARNING)

    def __init__(self, is_reset: bool = False):
        if is_reset:
            return
        if not self._MANAGER_CLASS_REGISTERED:
            raise ValueError(
                f'class not registered; use the `{register_trace_manager_class.__name__}` decorator on the subclass'
            )
        super().__init__()
        self._has_fancy_sys_tracing = (sys.version_info >= (3, 7))
        self._event_handlers = defaultdict(list)
        events_with_registered_handlers = set()
        for clazz in reversed(self.__class__.mro()):
            for evt, handlers in self.EVENT_HANDLERS_BY_CLASS.get(clazz, {}).items():
                self._event_handlers[evt].extend(handlers)
                if not issubclass(BaseTracerStateMachine, clazz) and len(handlers) > 0:
                    events_with_registered_handlers.add(evt)
        self.events_with_registered_handlers: FrozenSet[TraceEvent] = frozenset(events_with_registered_handlers)
        self.tracing_enabled = False
        self.sys_tracer = self._sys_tracer
        self.existing_tracer = None

        # ast-related fields
        self.ast_node_by_id: Dict[int, ast.AST] = {}
        self.parent_node_by_id: Dict[int, ast.AST] = {}
        self.augmented_node_ids_by_spec: Dict[AugmentationSpec, Set[int]] = defaultdict(set)
        self.line_to_stmt_by_module_id: Dict[int, Dict[int, ast.stmt]] = defaultdict(dict)
        self.node_id_to_containing_stmt: Dict[int, ast.stmt] = {}
        self.guards: Set[str] = set()

        self._transient_fields: Set[str] = set()
        self._persistent_fields: Set[str] = set()
        self._manual_persistent_fields: Set[str] = set()
        self._transient_fields_start()

    @property
    def has_sys_trace_events(self):
        return any(evt in self.events_with_registered_handlers for evt in (
            TraceEvent.line,
            TraceEvent.call,
            TraceEvent.return_,
            TraceEvent.exception,
            TraceEvent.opcode,
            TraceEvent.c_call,
            TraceEvent.c_return,
            TraceEvent.c_exception,
        ))

    @property
    def syntax_augmentation_specs(self) -> List[AugmentationSpec]:
        return []

    @property
    def should_patch_meta_path(self) -> bool:
        return True

    def _transient_fields_start(self):
        self._persistent_fields = set(self.__dict__.keys())

    def _transient_fields_end(self):
        self._transient_fields = set(self.__dict__.keys()) - self._persistent_fields - self._manual_persistent_fields

    @contextmanager
    def persistent_fields(self) -> Generator[None, None, None]:
        current_fields = set(self.__dict__.keys())
        saved_fields = {}
        for field in self._manual_persistent_fields:
            if field in current_fields:
                saved_fields[field] = self.__dict__[field]
        yield
        self._manual_persistent_fields = (self.__dict__.keys() - current_fields) | saved_fields.keys()
        for field, val in saved_fields.items():
            self.__dict__[field] = val

    def reset(self):
        for field in self._transient_fields:
            del self.__dict__[field]
        self.__init__(is_reset=True)

    def activate_guard(self, guard: str) -> None:
        assert guard in self.guards
        setattr(builtins, guard, False)

    def deactivate_guard(self, guard: str) -> None:
        assert guard in self.guards
        setattr(builtins, guard, True)

    def should_propagate_handler_exception(self, evt: TraceEvent, exc: Exception) -> bool:
        return False

    def _emit_event(self, evt: Union[TraceEvent, str], node_id: int, **kwargs: Any):
        try:
            event = TraceEvent(evt) if isinstance(evt, str) else evt
            frame = kwargs.get('_frame', sys._getframe().f_back)
            kwargs['_frame'] = frame
            for handler in self._event_handlers[event]:
                try:
                    new_ret = handler(self, kwargs.get('ret', None), node_id, frame, event, **kwargs)
                except Exception as exc:
                    if self.should_propagate_handler_exception(event, exc):
                        raise exc
                    else:
                        logger.exception('An exception while handling evt %s', evt)
                    new_ret = None
                if new_ret is not None:
                    kwargs['ret'] = new_ret
            return kwargs.get('ret', None)
        except KeyboardInterrupt as ki:
            self._disable_tracing(check_enabled=False)
            raise ki.with_traceback(None)

    def _make_stack(self):
        return TraceStack(self)

    def _make_composed_tracer(self, existing_tracer):  # pragma: no cover

        @functools.wraps(self._sys_tracer)
        def _composed_tracer(frame: FrameType, evt: str, arg: Any, **kwargs):
            existing_ret = existing_tracer(frame, evt, arg, **kwargs)
            if not self.tracing_enabled:
                return existing_ret
            my_ret = self._sys_tracer(frame, evt, arg, **kwargs)
            if my_ret is None and evt == 'call':
                return existing_ret
            else:
                return my_ret
        return _composed_tracer

    def _settrace_patch(self, trace_func):  # pragma: no cover
        # called by third-party tracers
        self.existing_tracer = trace_func
        if self.tracing_enabled:
            if trace_func is None:
                self._disable_tracing()
            self._enable_tracing(check_disabled=False, existing_tracer=trace_func)
        else:
            sys_settrace(trace_func)

    def _enable_tracing(self, check_disabled=True, existing_tracer=None):
        if check_disabled:
            assert not self.tracing_enabled
        self.tracing_enabled = True
        if self.has_sys_trace_events:
            self.existing_tracer = existing_tracer or sys.gettrace()
            if self.existing_tracer is None:
                self.sys_tracer = self._sys_tracer
            else:
                self.sys_tracer = self._make_composed_tracer(self.existing_tracer)
            sys_settrace(self.sys_tracer)
        setattr(builtins, TRACING_ENABLED, True)

    def _disable_tracing(self, check_enabled=True):
        has_sys_trace_events = self.has_sys_trace_events
        if check_enabled:
            assert self.tracing_enabled
            assert not has_sys_trace_events or sys.gettrace() is self.sys_tracer
        self.tracing_enabled = False
        if has_sys_trace_events:
            sys_settrace(self.existing_tracer)
        setattr(builtins, TRACING_ENABLED, False)

    @contextmanager
    def _patch_sys_settrace(self) -> Generator[None, None, None]:
        original_settrace = sys.settrace
        try:
            sys.settrace = self._settrace_patch
            yield
        finally:
            sys.settrace = original_settrace

    def should_trace_source_path(self, path) -> bool:
        return not path.startswith(internal_directories)

    def make_ast_rewriter(self, module_id: Optional[int] = None) -> AstRewriter:
        return self.ast_rewriter_cls(self, module_id=module_id)

    def make_syntax_augmenters(self, ast_rewriter: AstRewriter) -> List[Callable]:
        return [make_syntax_augmenter(ast_rewriter, spec) for spec in self.syntax_augmentation_specs]

    @contextmanager
    def _patch_meta_path(self) -> Generator[None, None, None]:
        if self.should_patch_meta_path:
            try:
                sys.meta_path.insert(0, TraceFinder(self))
                yield
            finally:
                del sys.meta_path[0]
        else:
            yield

    @contextmanager
    def tracing_context(self) -> Generator[None, None, None]:
        setattr(builtins, EMIT_EVENT, self._emit_event)
        for guard in self.guards:
            self.deactivate_guard(guard)
        try:
            with self._patch_meta_path():
                with self._patch_sys_settrace():
                    self._enable_tracing()
                    yield
        finally:
            self._disable_tracing(check_enabled=False)
            delattr(builtins, EMIT_EVENT)
            delattr(builtins, TRACING_ENABLED)
            for guard in self.guards:
                if hasattr(builtins, guard):
                    delattr(builtins, guard)

    def _should_attempt_to_reenable_tracing(self, frame: FrameType) -> bool:
        return NotImplemented

    def file_passes_filter_for_event(self, evt: str, filename: str) -> bool:
        return self.should_trace_source_path(filename)

    def _sys_tracer(self, frame: FrameType, evt: str, arg: Any, **__):
        if not self.file_passes_filter_for_event(evt, frame.f_code.co_filename):
            return None

        if self._has_fancy_sys_tracing and evt == "call":
            if TraceEvent.line not in self.events_with_registered_handlers:
                frame.f_trace_lines = False  # type: ignore
            if TraceEvent.opcode in self.events_with_registered_handlers:
                frame.f_trace_opcodes = True  # type: ignore

        return self._emit_event(evt, 0, _frame=frame, ret=arg)


def register_handler(event: Union[TraceEvent, Tuple[TraceEvent, ...]]):
    events = event if isinstance(event, tuple) else (event,)

    if TraceEvent.opcode in events and sys.version_info < (3, 7):
        raise ValueError("can't trace opcodes on Python < 3.7")

    def _inner_registrar(handler):
        for evt in events:
            SingletonTracerStateMachine.EVENT_HANDLERS_PENDING_REGISTRATION[evt].append(handler)
        return handler
    return _inner_registrar


def skip_when_tracing_disabled(handler):
    @functools.wraps(handler)
    def skipping_handler(self, *args, **kwargs):
        if not self.tracing_enabled:
            return
        return handler(self, *args, **kwargs)
    return skipping_handler


def register_universal_handler(handler):
    return register_handler(tuple(evt for evt in TraceEvent))(handler)


def register_trace_manager_class(mgr_cls: Type[SingletonTracerStateMachine]) -> Type[SingletonTracerStateMachine]:
    mgr_cls.EVENT_HANDLERS_BY_CLASS[mgr_cls] = defaultdict(list, mgr_cls.EVENT_HANDLERS_PENDING_REGISTRATION)
    mgr_cls.EVENT_HANDLERS_PENDING_REGISTRATION.clear()
    mgr_cls._MANAGER_CLASS_REGISTERED = True
    return mgr_cls


@register_trace_manager_class
class BaseTracerStateMachine(SingletonTracerStateMachine):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_slice: Optional[Any] = None

    @register_handler(TraceEvent.subscript)
    def _save_slice_for_later(self, *_, attr_or_subscript: Any, **__):
        self._saved_slice = attr_or_subscript

    @register_handler(TraceEvent._load_saved_slice)
    def _load_saved_slice(self, *_, **__):
        ret = self._saved_slice
        self._saved_slice = None
        return ret


assert not SingletonTracerStateMachine._MANAGER_CLASS_REGISTERED
assert BaseTracerStateMachine._MANAGER_CLASS_REGISTERED
