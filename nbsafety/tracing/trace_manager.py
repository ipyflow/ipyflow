# -*- coding: future_annotations -*-
import ast
import builtins
from collections import defaultdict
from contextlib import contextmanager
import functools
import logging
import sys
from typing import cast, TYPE_CHECKING

import astunparse
from IPython import get_ipython

from nbsafety import singletons
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.data_model.scope import Scope, NamespaceScope
from nbsafety.run_mode import SafetyRunMode
from nbsafety.singletons import nbs
from nbsafety.tracing.mutation_event import ArgMutate, ListAppend, ListExtend, ListInsert, StandardMutation
from nbsafety.tracing.symbol_resolver import resolve_rval_symbols
from nbsafety.tracing.trace_events import TraceEvent, EMIT_EVENT
from nbsafety.tracing.trace_stack import TraceStack
from nbsafety.tracing.trace_stmt import TraceStatement
from nbsafety.tracing.utils import match_container_obj_or_namespace_with_literal_nodes

if TYPE_CHECKING:
    from typing import Any, Callable, DefaultDict, Dict, List, Optional, Set, Tuple, Type, Union
    from types import FrameType
    from nbsafety.tracing.mutation_event import MutationEvent
    from nbsafety.types import SupportedIndexType
    AttrSubVal = SupportedIndexType
    NodeId = int
    ObjId = int
    MutationCandidate = Tuple[Tuple[int, ObjId, Optional[str]], MutationEvent, Set[DataSymbol], List[Any]]
    Mutation = Tuple[int, MutationEvent, Set[DataSymbol], List[Any]]
    SavedStoreData = Tuple[NamespaceScope, Any, AttrSubVal, bool]
    SavedDelData = Tuple[NamespaceScope, AttrSubVal, bool]
    SavedComplexSymbolLoadData = Tuple[Tuple[NamespaceScope, Any], Tuple[AttrSubVal, bool]]


logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


ARG_MUTATION_EXCEPTED_MODULES = {
    'alt',
    'altair',
    'display',
    'logging',
    'matplotlib',
    'pyplot',
    'plot',
    'plt',
    'seaborn',
    'sns',
    'widget',
}


def _finish_tracing_reset():
    # do nothing; we just want to trigger the newly reenabled tracer with a 'call' event
    pass


class BaseTraceManager(singletons.TraceManager):

    _MANAGER_CLASS_REGISTERED = False
    EVENT_HANDLERS_PENDING_REGISTRATION: DefaultDict[TraceEvent, List[Callable[..., Any]]] = defaultdict(list)
    EVENT_HANDLERS_BY_CLASS: Dict[Type[BaseTraceManager], DefaultDict[TraceEvent, List[Callable[..., Any]]]] = {}

    EVENT_LOGGER = logging.getLogger('events')
    EVENT_LOGGER.setLevel(logging.WARNING)

    def __init__(self):
        if not self._MANAGER_CLASS_REGISTERED:
            raise ValueError(
                f'class not registered; use the `{register_trace_manager_class.__name__}` decorator on the subclass'
            )
        super().__init__()
        self._event_handlers = self.EVENT_HANDLERS_BY_CLASS[self.__class__]
        self.tracing_enabled = False
        self.tracing_reset_pending = False
        self.sys_tracer = self._sys_tracer
        self.existing_tracer = None

    def _emit_event(self, evt: Union[TraceEvent, str], node_id: int, **kwargs: Any):
        event = TraceEvent(evt) if isinstance(evt, str) else evt
        frame = kwargs.get('_frame', sys._getframe().f_back)
        kwargs['_frame'] = frame
        for handler in self._event_handlers[event]:
            try:
                new_ret = handler(self, kwargs.get('ret', None), node_id, frame, event, **kwargs)
            except Exception as exc:
                if SafetyRunMode.get() == SafetyRunMode.DEVELOP:
                    raise exc
                else:
                    logger.error('Exception occurred: %s', str(exc))
                new_ret = None
            if new_ret is not None:
                kwargs['ret'] = new_ret
        return kwargs.get('ret', None)

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
            nbs().settrace(trace_func)

    def _enable_tracing(self, check_disabled=True, existing_tracer=None):
        if check_disabled:
            assert not self.tracing_enabled
        self.tracing_enabled = True
        self.existing_tracer = existing_tracer or sys.gettrace()
        if self.existing_tracer is None:
            self.sys_tracer = self._sys_tracer
        else:
            self.sys_tracer = self._make_composed_tracer(self.existing_tracer)
        nbs().settrace(self.sys_tracer)

    def _disable_tracing(self, check_enabled=True):
        if check_enabled:
            assert self.tracing_enabled
            assert sys.gettrace() is self.sys_tracer
        self.tracing_enabled = False
        nbs().settrace(self.existing_tracer)

    @contextmanager
    def _patch_sys_settrace(self):
        original_settrace = sys.settrace
        try:
            sys.settrace = self._settrace_patch
            yield
        finally:
            sys.settrace = original_settrace

    @contextmanager
    def tracing_context(self):
        setattr(builtins, EMIT_EVENT, self._emit_event)
        try:
            with self._patch_sys_settrace():
                self._enable_tracing()
                yield
        finally:
            delattr(builtins, EMIT_EVENT)
            self._disable_tracing(check_enabled=False)

    def _attempt_to_reenable_tracing(self, frame: FrameType):
        return NotImplemented

    def _sys_tracer(self, frame: FrameType, evt: str, arg: Any, **__):
        if self.tracing_reset_pending:
            assert evt == 'call', 'expected call; got event %s' % evt
            self._attempt_to_reenable_tracing(frame)
            return None
        if evt == 'line' or not nbs().is_cell_file(frame.f_code.co_filename):
            return None

        return self._emit_event(evt, 0, _frame=frame, ret=arg)


def register_handler(event: Union[TraceEvent, Tuple[TraceEvent, ...]]):
    events = event if isinstance(event, tuple) else (event,)

    def _inner_registrar(handler):
        for evt in events:
            BaseTraceManager.EVENT_HANDLERS_PENDING_REGISTRATION[evt].append(handler)
        return handler
    return _inner_registrar


def register_universal_handler(handler):
    return register_handler(tuple(evt for evt in TraceEvent))(handler)


def register_trace_manager_class(mgr_cls: Type[BaseTraceManager]) -> Type[BaseTraceManager]:
    mgr_cls.EVENT_HANDLERS_BY_CLASS[mgr_cls] = defaultdict(list, mgr_cls.EVENT_HANDLERS_PENDING_REGISTRATION)
    mgr_cls.EVENT_HANDLERS_PENDING_REGISTRATION.clear()
    mgr_cls._MANAGER_CLASS_REGISTERED = True
    return mgr_cls


@register_trace_manager_class
class TraceManager(BaseTraceManager):
    def __init__(self):
        super().__init__()
        self.trace_event_counter = 0
        self.prev_event: Optional[TraceEvent] = None
        self.prev_trace_stmt: Optional[TraceStatement] = None
        self.seen_stmts: Set[NodeId] = set()
        self.call_depth = 0
        self.traced_statements: Dict[NodeId, TraceStatement] = {}
        self.node_id_to_loaded_symbols: Dict[NodeId, List[DataSymbol]] = defaultdict(list)
        self.node_id_to_saved_store_data: Dict[NodeId, SavedStoreData] = {}
        self.node_id_to_saved_del_data: Dict[NodeId, SavedDelData] = {}
        self.node_id_to_loaded_literal_scope: Dict[NodeId, NamespaceScope] = {}
        self.node_id_to_saved_dict_key: Dict[NodeId, Any] = {}

        self.call_stack: TraceStack = self._make_stack()
        with self.call_stack.register_stack_state():
            # everything here should be copyable
            self.prev_trace_stmt_in_cur_frame: Optional[TraceStatement] = None
            self.prev_node_id_in_cur_frame: Optional[NodeId] = None
            self.mutations: List[Mutation] = []
            self.mutation_candidates: List[MutationCandidate] = []
            self.saved_assign_rhs_obj_id: Optional[int] = None

            with self.call_stack.needing_manual_initialization():
                self.cur_frame_original_scope: Scope = nbs().global_scope
                self.active_scope: Scope = nbs().global_scope
                self.inside_anonymous_call = False

            self.lexical_call_stack: TraceStack = self._make_stack()
            with self.lexical_call_stack.register_stack_state():
                self.num_args_seen = 0
                self.sym_for_obj_calling_method: Optional[DataSymbol] = None
                self.first_obj_id_in_chain: Optional[ObjId] = None
                self.top_level_node_id_for_chain: Optional[NodeId] = None
                self.saved_complex_symbol_load_data: Optional[SavedComplexSymbolLoadData] = None
                self.prev_node_id_in_cur_frame_lexical: Optional[NodeId] = None

            self.lexical_literal_stack: TraceStack = self._make_stack()
            with self.lexical_literal_stack.register_stack_state():
                # `None` means use 'cur_frame_original_scope'
                self.active_literal_scope: Optional[NamespaceScope] = None

    # TODO: use stack mechanism to automate this?
    def after_stmt_reset_hook(self):
        self.mutations.clear()
        self.mutation_candidates.clear()
        self.lexical_call_stack.clear()
        self.lexical_literal_stack.clear()
        self.active_scope = self.cur_frame_original_scope
        self.first_obj_id_in_chain = None
        self.top_level_node_id_for_chain = None
        self.saved_complex_symbol_load_data = None
        self.active_literal_scope = None
        self.node_id_to_loaded_literal_scope.clear()
        self.node_id_to_saved_dict_key.clear()

    def _handle_call_transition(self, trace_stmt: TraceStatement):
        # ensures we only handle del's and not delitem's
        self.node_id_to_saved_del_data.clear()
        new_scope = trace_stmt.get_post_call_scope()
        with self.call_stack.push():
            # TODO: figure out a better way to determine if we're inside a lambda
            #  could this one lead to a false negative if a lambda is in the default of a function def kwarg?
            self.inside_anonymous_call = not isinstance(
                trace_stmt.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
            self.cur_frame_original_scope = new_scope
            self.active_scope = new_scope
        self.prev_trace_stmt_in_cur_frame = self.prev_trace_stmt = trace_stmt

    def _check_prev_stmt_done_executing_hook(self, event: TraceEvent, trace_stmt: TraceStatement):
        if event == TraceEvent.after_stmt:
            trace_stmt.finished_execution_hook()
        elif event == TraceEvent.return_ and self.prev_event not in (TraceEvent.call, TraceEvent.exception):
            # ensuring prev != call ensures we're not inside of a stmt with multiple calls (such as map w/ lambda)
            if self.prev_trace_stmt is not None:
                self.prev_trace_stmt.finished_execution_hook()
            # prev_overall = self.prev_trace_stmt
            # if prev_overall is not None and prev_overall is not self._stack[-1][0]:
            #     # this condition ensures we're not inside of a stmt with multiple calls (such as map w/ lambda)
            #     prev_overall.finished_execution_hook()

    def _handle_return_transition(self, trace_stmt: TraceStatement, ret: Any):
        try:
            inside_anonymous_call = self.inside_anonymous_call
            return_to_stmt: TraceStatement = self.call_stack.get_field('prev_trace_stmt_in_cur_frame')
            assert return_to_stmt is not None
            if self.prev_event != TraceEvent.exception:
                # exception events are followed by return events until we hit an except clause
                # no need to track dependencies in this case
                if isinstance(return_to_stmt.stmt_node, ast.ClassDef):
                    return_to_stmt.class_scope = cast(NamespaceScope, self.cur_frame_original_scope)
                elif isinstance(trace_stmt.stmt_node, ast.Return) or inside_anonymous_call:
                    if not trace_stmt.lambda_call_point_deps_done_once:
                        trace_stmt.lambda_call_point_deps_done_once = True
                        maybe_lambda_sym = nbs().statement_to_func_cell.get(id(trace_stmt.stmt_node), None)
                        maybe_lambda_node = None
                        if maybe_lambda_sym is not None:
                            maybe_lambda_node = maybe_lambda_sym.stmt_node
                        if inside_anonymous_call and maybe_lambda_node is not None and isinstance(maybe_lambda_node, ast.Lambda):
                            rvals = resolve_rval_symbols(maybe_lambda_node.body)
                        else:
                            rvals = resolve_rval_symbols(trace_stmt.stmt_node)
                        dsym_to_attach = None
                        if len(rvals) == 1:
                            dsym_to_attach = next(iter(rvals))
                            if dsym_to_attach.obj_id != id(ret):
                                dsym_to_attach = None
                        if dsym_to_attach is None and len(rvals) > 0:
                            dsym_to_attach = self.cur_frame_original_scope.upsert_data_symbol_for_name(
                                '<return_sym_%d>' % id(ret), ret, rvals, trace_stmt.stmt_node, is_anonymous=True
                            )
                        if dsym_to_attach is not None:
                            return_to_node_id = self.call_stack.get_field('prev_node_id_in_cur_frame')
                            # logger.error("prev seen: %s", ast.dump(nbs().ast_node_by_id[return_to_node_id]))
                            try:
                                call_node_id = self.call_stack.get_field(
                                    'lexical_call_stack'
                                ).get_field('prev_node_id_in_cur_frame_lexical')
                                call_node = cast(ast.Call, nbs().ast_node_by_id[call_node_id])
                                # logger.error("prev seen outer: %s", ast.dump(nbs().ast_node_by_id[call_node_id]))
                                total_args = len(call_node.args) + len(call_node.keywords)
                                num_args_seen = self.call_stack.get_field('num_args_seen')
                                logger.warning("num args seen: %d", num_args_seen)
                                if total_args == num_args_seen:
                                    return_to_node_id = call_node_id
                                else:
                                    assert num_args_seen < total_args
                                    if num_args_seen < len(call_node.args):
                                        return_to_node_id = id(call_node.args[num_args_seen])
                                    else:
                                        return_to_node_id = id(call_node.keywords[num_args_seen - len(call_node.args)].value)
                            except IndexError:
                                pass
                            # logger.error("use node %s", ast.dump(nbs().ast_node_by_id[return_to_node_id]))
                            self.node_id_to_loaded_symbols[return_to_node_id].append(dsym_to_attach)
        finally:
            self.call_stack.pop()

    def state_transition_hook(
        self,
        event: TraceEvent,
        trace_stmt: TraceStatement,
        ret: Any,
    ):
        self.trace_event_counter += 1

        self._check_prev_stmt_done_executing_hook(event, trace_stmt)

        if event == TraceEvent.call:
            self._handle_call_transition(trace_stmt)
        if event == TraceEvent.return_:
            self._handle_return_transition(trace_stmt, ret)
        self.prev_event = event

    @staticmethod
    def _partial_resolve_ref(ref: Union[str, int, ast.AST]) -> Union[str, int]:
        if isinstance(ref, ast.Starred):
            ref = ref.value
        if isinstance(ref, ast.Name):
            ref = ref.id
        if isinstance(ref, ast.AST):
            ref = id(ref)
        return ref

    def resolve_store_data_for_target(
        self, target: Union[str, int, ast.AST], frame: FrameType
    ) -> Tuple[Scope, AttrSubVal, Any, bool]:
        target = self._partial_resolve_ref(target)
        if isinstance(target, str):
            obj = frame.f_locals[target]
            return self.cur_frame_original_scope, target, obj, False
        (
            scope, obj, attr_or_sub, is_subscript
        ) = self.node_id_to_saved_store_data[target]
        attr_or_sub_obj = nbs().retrieve_namespace_attr_or_sub(obj, attr_or_sub, is_subscript)
        if attr_or_sub_obj is None:
            scope_to_use = scope
        else:
            scope_to_use = scope.get_earliest_ancestor_containing(id(attr_or_sub_obj), is_subscript)
        if scope_to_use is None:
            # Nobody before `scope` has it, so we'll insert it at this level
            scope_to_use = scope
        return scope_to_use, attr_or_sub, attr_or_sub_obj, is_subscript

    def resolve_del_data_for_target(self, target: Union[str, int, ast.AST]) -> Tuple[Scope, AttrSubVal, Any, bool]:
        target = self._partial_resolve_ref(target)
        if isinstance(target, str):
            return self.cur_frame_original_scope, target, None, False
        (
            scope, attr_or_sub, is_subscript
        ) = self.node_id_to_saved_del_data[target]
        return scope, attr_or_sub, None, is_subscript

    def resolve_loaded_symbols(self, symbol_ref: Union[str, int, ast.AST, DataSymbol]) -> List[DataSymbol]:
        if isinstance(symbol_ref, DataSymbol):
            return [symbol_ref]
        symbol_ref = self._partial_resolve_ref(symbol_ref)
        if isinstance(symbol_ref, int):
            return self.node_id_to_loaded_symbols.get(symbol_ref, [])
        elif isinstance(symbol_ref, str):
            return [self.cur_frame_original_scope.lookup_data_symbol_by_name(symbol_ref)]
        else:
            return []

    def resolve_symbols(self, symbol_refs: Set[Union[str, int, DataSymbol]]) -> Set[DataSymbol]:
        data_symbols = set()
        for ref in symbol_refs:
            data_symbols.update(self.resolve_loaded_symbols(ref))
        return data_symbols

    def _get_namespace_for_obj(self, obj: Any, obj_name: Optional[str] = None) -> NamespaceScope:
        obj_id = id(obj)
        ns = nbs().namespaces.get(obj_id, None)
        if ns is not None:
            return ns
        class_scope = nbs().namespaces.get(id(obj.__class__), None)
        if class_scope is not None:
            # logger.warning(
            #     'found class scope %s containing %s',
            #     class_scope, list(class_scope.all_data_symbols_this_indentation())
            # )
            ns = class_scope.clone(obj)
            if obj_name is not None:
                ns.scope_name = obj_name
        else:
            # print('no scope for class', obj.__class__)
            try:
                scope_name = nbs().get_first_full_symbol(obj_id).name if obj_name is None else obj_name
            except AttributeError:
                scope_name = '<unknown namespace>'
            ns = NamespaceScope(obj, scope_name, parent_scope=None)
        # FIXME: brittle strategy for determining parent scope of obj
        if ns.parent_scope is None:
            if (
                obj_name is not None and
                obj_name not in self.prev_trace_stmt_in_cur_frame.frame.f_locals
            ):
                parent_scope = nbs().global_scope
            else:
                parent_scope = self.active_scope
            ns.parent_scope = parent_scope
        return ns

    def _clear_info_and_maybe_lookup_or_create_complex_symbol(self, obj_attr_or_sub) -> Optional[DataSymbol]:
        if self.saved_complex_symbol_load_data is None:
            return None
        (scope, obj), (attr_or_subscript, is_subscript) = self.saved_complex_symbol_load_data
        self.saved_complex_symbol_load_data = None
        data_sym = scope.lookup_data_symbol_by_name_this_indentation(
            attr_or_subscript, is_subscript=is_subscript, skip_cloned_lookup=True,
        )
        logger.warning("found sym %s in scope %s", data_sym, scope)
        if data_sym is None:
            parent = scope.lookup_data_symbol_by_name_this_indentation(
                attr_or_subscript, is_subscript, skip_cloned_lookup=False,
            )
            parents = set() if parent is None else {parent}
            is_default_dict = isinstance(obj, defaultdict)
            data_sym = scope.upsert_data_symbol_for_name(
                attr_or_subscript,
                obj_attr_or_sub,
                parents,
                self.prev_trace_stmt_in_cur_frame.stmt_node,
                is_subscript=is_subscript,
                propagate=is_default_dict,
                implicit=not is_default_dict,
            )
            logger.warning("create implicit sym %s", data_sym)
        elif data_sym.obj_id != id(obj_attr_or_sub):
            data_sym.update_obj_ref(obj_attr_or_sub)
        return data_sym

    @register_handler(
        # all the AST-related events
        tuple(set(TraceEvent) - {
            TraceEvent.call,
            TraceEvent.return_,
            TraceEvent.exception,
            TraceEvent.c_call,
            TraceEvent.c_return,
            TraceEvent.c_exception,
            TraceEvent.argument,
        })
    )
    def _save_node_id(self, _obj, node_id: NodeId, *_, **__):
        self.prev_node_id_in_cur_frame = node_id
        self.prev_node_id_in_cur_frame_lexical = node_id

    @register_handler(TraceEvent.init_cell)
    def init_cell(self, _obj, _node_id, frame: FrameType, _event, cell_id: Union[str, int], **__):
        nbs().set_name_to_cell_num_mapping(frame)

    @register_handler(TraceEvent.after_assign_rhs)
    def after_assign_rhs(self, obj: Any, *_, **__):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return
        self.saved_assign_rhs_obj_id = id(obj)

    @register_handler((TraceEvent.attribute, TraceEvent.subscript))
    def attrsub_tracer(
        self,
        obj: Any,
        node_id: NodeId,
        _frame_: FrameType,
        event: TraceEvent,
        *_,
        attr_or_subscript: AttrSubVal,
        ctx: str,
        call_context: bool,
        top_level_node_id: NodeId,
        obj_name: Optional[str] = None,
        **__
    ):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return
        if isinstance(nbs().ast_node_by_id[node_id], ast.Call):
            # clear the callpoint dependency
            self.node_id_to_loaded_symbols.pop(node_id, None)
        if obj is None or obj is get_ipython():
            return
        logger.warning('%s attrsub %s of obj %s', ctx, attr_or_subscript, obj)
        sym_for_obj = self._clear_info_and_maybe_lookup_or_create_complex_symbol(obj)
        
        # Resolve symbol if necessary
        if sym_for_obj is None and obj_name is not None:
            sym_for_obj = self.active_scope.lookup_data_symbol_by_name_this_indentation(obj_name)

        if sym_for_obj is not None and sym_for_obj.timestamp < nbs().cell_counter():
            sym_for_obj.timestamp_by_used_time[nbs().cell_counter()] = sym_for_obj.timestamp_excluding_ns_descendents
        
        is_subscript = (event == TraceEvent.subscript)

        obj_id = id(obj)
        if self.top_level_node_id_for_chain is None:
            self.top_level_node_id_for_chain = top_level_node_id
        if self.first_obj_id_in_chain is None:
            self.first_obj_id_in_chain = obj_id
        if isinstance(attr_or_subscript, tuple):
            if not all(isinstance(v, (str, int)) for v in attr_or_subscript):
                return
        elif not isinstance(attr_or_subscript, (str, int)):
            return

        scope = self._get_namespace_for_obj(obj, obj_name=obj_name)
        self.active_scope = scope

        if ctx in ('Store', 'AugStore'):
            logger.warning(
                "save store data for node id %d: %s, %s, %s, %s",
                top_level_node_id, scope, obj, attr_or_subscript, is_subscript
            )
            self.node_id_to_saved_store_data[top_level_node_id] = (scope, obj, attr_or_subscript, is_subscript)
            return
        elif ctx == 'Del':
            # logger.error("save del data for node %s", ast.dump(nbs().ast_node_by_id[top_level_node_id]))
            logger.warning("save del data for node id %d", top_level_node_id)
            self.node_id_to_saved_del_data[top_level_node_id] = (scope, attr_or_subscript, is_subscript)
            return
        if call_context:
            mutation_event: MutationEvent = StandardMutation()
            if isinstance(obj, list):
                if attr_or_subscript == 'append':
                    mutation_event = ListAppend()
                elif attr_or_subscript == 'extend':
                    mutation_event = ListExtend(len(obj))
                elif attr_or_subscript == 'insert':
                    mutation_event = ListInsert()
            # save off event counter and obj_id
            # if event counter didn't change when we process the Call retval, and if the
            # retval is None, this is a likely signal that we have a mutation
            self.mutation_candidates.append(
                ((self.trace_event_counter, obj_id, obj_name), mutation_event, set(), [])
            )
            if not is_subscript:
                if sym_for_obj is None and obj_name is not None:
                    sym_for_obj = self.cur_frame_original_scope.lookup_data_symbol_by_name(
                        obj_name, is_subscript=is_subscript
                    )
                if sym_for_obj is None and self.prev_trace_stmt_in_cur_frame is not None:
                    sym_for_obj = self.cur_frame_original_scope.upsert_data_symbol_for_name(
                        obj_name or '<anonymous_symbol_%d>' % id(obj),
                        obj,
                        set(),
                        self.prev_trace_stmt_in_cur_frame.stmt_node,
                        is_subscript=is_subscript,
                        is_anonymous=obj_name is None,
                        propagate=False,
                        implicit=True,
                    )
                if sym_for_obj is not None:
                    self.sym_for_obj_calling_method = sym_for_obj
        else:
            logger.warning("saved load data: %s, %s, %s", scope, attr_or_subscript, is_subscript)
            self.saved_complex_symbol_load_data = ((scope, obj), (attr_or_subscript, is_subscript))

    @register_handler(TraceEvent.after_complex_symbol)
    def after_complex_symbol(self, obj: Any, *_, call_context: bool, ctx: str, **__):
        try:
            if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
                return
            if self.first_obj_id_in_chain is None:
                return
            if ctx != 'Load':
                # don't trace after non-load events
                return
            loaded_sym = self._clear_info_and_maybe_lookup_or_create_complex_symbol(obj)
            if call_context and len(self.mutation_candidates) > 0:
                (
                    (evt_counter, obj_id, obj_name),
                    mutation_event,
                    recorded_arg_dsyms,
                    recorded_arg_objs,
                ) = self.mutation_candidates.pop()
                if evt_counter == self.trace_event_counter:
                    if obj is None or id(obj) == obj_id:
                        if isinstance(mutation_event, StandardMutation):
                            try:
                                top_level_sym = nbs().get_first_full_symbol(self.first_obj_id_in_chain)
                                if top_level_sym.is_import and top_level_sym.name not in ARG_MUTATION_EXCEPTED_MODULES:
                                    # TODO: should it be the other way around?
                                    #  i.e. allow-list for arg mutations, starting with np.random.seed?
                                    if len(recorded_arg_dsyms) > 0:
                                        # only make this an arg mutation event if it looks like there's an arg to mutate
                                        mutation_event = ArgMutate()
                            except:
                                pass
                        elif isinstance(mutation_event, ListInsert):
                            mutation_event.insert_pos = recorded_arg_objs[0]
                        self.mutations.append((obj_id, mutation_event, recorded_arg_dsyms, recorded_arg_objs))
                    else:
                        if self.sym_for_obj_calling_method is not None:
                            loaded_sym = self.sym_for_obj_calling_method
                            self.sym_for_obj_calling_method = None
            if loaded_sym is not None:
                self.node_id_to_loaded_symbols[self.top_level_node_id_for_chain].append(loaded_sym)
        finally:
            self.saved_complex_symbol_load_data = None
            self.first_obj_id_in_chain = None
            self.top_level_node_id_for_chain = None
            self.sym_for_obj_calling_method = None
            self.active_scope = self.cur_frame_original_scope

    @register_handler(TraceEvent.argument)
    def argument(self, arg_obj: Any, arg_node_id: int, *_, **__):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return
        self.num_args_seen += 1
        arg_node = nbs().ast_node_by_id.get(arg_node_id, None)
        if len(self.mutation_candidates) == 0:
            return

        if isinstance(arg_node, ast.Name):
            assert self.active_scope is self.cur_frame_original_scope
            arg_dsym = self.active_scope.lookup_data_symbol_by_name(arg_node.id)
            if arg_dsym is None:
                self.active_scope.upsert_data_symbol_for_name(
                    arg_node.id, arg_obj, set(), self.prev_trace_stmt_in_cur_frame.stmt_node, implicit=True
                )
        self.mutation_candidates[-1][-2].update(resolve_rval_symbols(arg_node))
        self.mutation_candidates[-1][-1].append(arg_obj)

    @register_handler(TraceEvent.before_call)
    def before_call(self, *_, **__):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return
        with self.lexical_call_stack.push():
            pass
        self.active_scope = self.cur_frame_original_scope

    @register_handler(TraceEvent.after_call)
    def after_call(self, *_, call_node_id: NodeId, **__):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return
        # no need to reset active scope here;
        # that will happen in the 'after chain' handler
        self.lexical_call_stack.pop()

    # Note: we don't trace set literals
    @register_handler((TraceEvent.before_dict_literal, TraceEvent.before_list_literal, TraceEvent.before_tuple_literal))
    def before_literal(self, *_, **__):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return
        parent_scope = self.active_literal_scope or self.cur_frame_original_scope
        with self.lexical_literal_stack.push():
            self.active_literal_scope = NamespaceScope(None, NamespaceScope.ANONYMOUS, parent_scope)

    @register_handler((TraceEvent.after_dict_literal, TraceEvent.after_list_literal, TraceEvent.after_tuple_literal))
    def after_literal(self, literal: Union[dict, list, tuple], node_id: NodeId, *_, **__):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return literal
        try:
            self.active_literal_scope.update_obj_ref(literal)
            logger.warning("create literal scope %s", self.active_literal_scope)
            starred_idx = -1
            starred_namespace = None
            for (i, inner_obj), (inner_key_node, inner_val_node) in match_container_obj_or_namespace_with_literal_nodes(
                literal, nbs().ast_node_by_id[node_id]  # type: ignore
            ):
                # TODO: memoize symbol resolution; otherwise this will be quadratic for deeply nested literals
                if isinstance(inner_val_node, ast.Starred):
                    inner_symbols = set()
                    starred_idx += 1
                    if starred_idx == 0:
                        starred_syms = self.resolve_loaded_symbols(inner_val_node)
                        starred_namespace = nbs().namespaces.get(starred_syms[0].obj_id, None) if starred_syms else None
                    if starred_namespace is not None:
                        starred_dep = starred_namespace.lookup_data_symbol_by_name_this_indentation(starred_idx, is_subscript=True)
                        inner_symbols.add(starred_dep)
                else:
                    inner_symbols = resolve_rval_symbols(inner_val_node)
                    if inner_key_node is not None:
                        # inner_symbols.add(self.resolve_loaded_symbols(inner_key_node))
                        inner_symbols.update(resolve_rval_symbols(inner_key_node))
                self.node_id_to_loaded_symbols.pop(id(inner_val_node), None)
                inner_symbols.discard(None)
                if isinstance(i, (int, str)):  # TODO: perform more general check for SupportedIndexType
                    self.active_literal_scope.upsert_data_symbol_for_name(
                        i, inner_obj, inner_symbols, self.prev_trace_stmt_in_cur_frame.stmt_node, is_subscript=True
                    )
            self.node_id_to_loaded_literal_scope[node_id] = self.active_literal_scope
            parent_scope: Scope = self.active_literal_scope.parent_scope
            assert parent_scope is not None
            literal_sym = parent_scope.upsert_data_symbol_for_name(
                '<literal_sym_%d>' % id(literal),
                literal,
                set(),
                self.prev_trace_stmt_in_cur_frame.stmt_node,
                is_anonymous=True,
            )
            self.node_id_to_loaded_symbols[node_id].append(literal_sym)
            return literal
        finally:
            self.lexical_literal_stack.pop()

    @register_handler(TraceEvent.dict_key)
    def dict_key(self, obj: Any, key_node_id: NodeId, *_, **__):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return obj
        self.node_id_to_saved_dict_key[key_node_id] = obj
        return obj

    @register_handler(TraceEvent.dict_value)
    def dict_value(self, obj: Any, value_node_id: NodeId, *_, key_node_id: NodeId, dict_node_id: NodeId, **__):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return obj
        scope = self.node_id_to_loaded_literal_scope.pop(value_node_id, None)
        if scope is None:
            return obj
        # if we found a pending literal, assert that it's not dict unpacking
        assert key_node_id is not None
        key_obj = self.node_id_to_saved_dict_key.pop(key_node_id, None)
        if isinstance(key_obj, (str, int)):
            scope.scope_name = str(key_obj)
        return obj

    @register_handler((TraceEvent.list_elt, TraceEvent.tuple_elt))
    def list_or_tuple_elt(
        self, obj: Any, elt_node_id: NodeId, *_, index: Optional[int], container_node_id: NodeId, **__
    ):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return obj
        scope = self.node_id_to_loaded_literal_scope.pop(elt_node_id, None)
        if scope is None:
            return obj
        if index is not None:
            scope.scope_name = str(index)
        return obj

    @register_handler(TraceEvent.after_lambda)
    def after_lambda(self, obj: Any, lambda_node_id: int, frame: FrameType, *_, **__):
        sym_deps = []
        node = nbs().ast_node_by_id[lambda_node_id]
        for kw_default in node.args.defaults:  # type: ignore
            sym_deps.extend(self.resolve_loaded_symbols(kw_default))
        sym = self.active_scope.upsert_data_symbol_for_name(
            '<lambda_sym_%d>' % id(obj),
            obj,
            sym_deps,
            self.prev_trace_stmt_in_cur_frame.stmt_node,
            is_function_def=True,
            propagate=False,
        )
        # FIXME: this is super brittle. We're passing in a stmt node to update the mapping from
        #  stmt_node to function symbol, but simultaneously forcing the lambda symbol to hold
        #  a reference to the lambda in order to help with symbol resolution later
        sym.stmt_node = node
        self.node_id_to_loaded_symbols[lambda_node_id].append(sym)

    @register_handler(TraceEvent.after_stmt)
    def after_stmt(self, ret_expr: Any, stmt_id: int, frame: FrameType, *_, **__):
        if stmt_id in self.seen_stmts:
            return ret_expr
        stmt = nbs().ast_node_by_id.get(stmt_id, None)
        if stmt is not None:
            self.handle_sys_events(None, 0, frame, TraceEvent.after_stmt, stmt_node=cast(ast.stmt, stmt))
        return ret_expr

    @register_handler(TraceEvent.before_stmt)
    def before_stmt(self, _ret: None, stmt_id: int, frame: FrameType, *_, **__) -> None:
        if stmt_id in self.seen_stmts:
            return
        # logger.warning('reenable tracing: %s', site_id)
        if self.prev_trace_stmt_in_cur_frame is not None:
            prev_trace_stmt_in_cur_frame = self.prev_trace_stmt_in_cur_frame
            # both of the following stmts should be processed when body is entered
            if isinstance(prev_trace_stmt_in_cur_frame.stmt_node, (ast.For, ast.If, ast.With)):
                self.after_stmt(None, prev_trace_stmt_in_cur_frame.stmt_id, frame)
        trace_stmt = self.traced_statements.get(stmt_id, None)
        if trace_stmt is None:
            trace_stmt = TraceStatement(frame, cast(ast.stmt, nbs().ast_node_by_id[stmt_id]))
            self.traced_statements[stmt_id] = trace_stmt
        self.prev_trace_stmt_in_cur_frame = trace_stmt
        self.prev_trace_stmt = trace_stmt
        if not self.tracing_enabled:
            assert not self.tracing_reset_pending
            self._enable_tracing()
            self.tracing_reset_pending = True
            _finish_tracing_reset()  # trigger the tracer with a frame

    def _attempt_to_reenable_tracing(self, frame: FrameType) -> None:
        if nbs().is_develop:
            assert self.tracing_reset_pending, 'expected tracing reset to be pending!'
            assert self.call_depth > 0, 'expected managed call depth > 0, got %d' % self.call_depth
        self.tracing_reset_pending = False
        call_depth = 0
        while frame is not None:
            if nbs().is_cell_file(frame.f_code.co_filename):
                call_depth += 1
            frame = frame.f_back
        if nbs().is_develop:
            assert call_depth >= 1, 'expected call depth >= 1, got %d' % call_depth
        # TODO: allow reenabling tracing beyond just at the top level
        if call_depth != 1:
            self._disable_tracing()
            return
        # at this point, we can be sure we're at the top level
        # because tracing was enabled in a handler and not in the
        # top level, we need to clear the stack, since we won't
        # catch the return event
        self.call_depth = 0
        self.call_stack.clear()
        self.lexical_call_stack.clear()
        if nbs().trace_messages_enabled:
            self.EVENT_LOGGER.warning('reenable tracing >>>')

    @register_handler((TraceEvent.call, TraceEvent.return_, TraceEvent.exception))
    def handle_sys_events(
        self,
        ret_obj: Any,
        _node_id: int,
        frame: FrameType,
        event: TraceEvent,
        *_,
        stmt_node: Optional[ast.stmt] = None,
        **__
    ):
        # right now, this should only be enabled for notebook code
        assert nbs().is_cell_file(frame.f_code.co_filename), 'got %s' % frame.f_code.co_filename
        assert self.tracing_enabled or event == TraceEvent.after_stmt

        # IPython quirk -- every line in outer scope apparently wrapped in lambda
        # We want to skip the outer 'call' and 'return' for these
        if event == TraceEvent.call:
            self.call_depth += 1
            if self.call_depth == 1:
                return self.sys_tracer

        if event == TraceEvent.return_:
            self.call_depth -= 1
            if self.call_depth == 0:
                return

        cell_num, lineno = nbs().get_position(frame)
        if cell_num is None:
            return None

        if event == TraceEvent.after_stmt:
            assert stmt_node is not None
        else:
            try:
                stmt_node = nbs().statement_cache[cell_num][lineno]
            except KeyError as e:
                self.EVENT_LOGGER.warning("got key error for stmt node in cell %d, line %d", cell_num, lineno)
                if nbs().is_develop:
                    raise e
                return self.sys_tracer

        trace_stmt = self.traced_statements.get(id(stmt_node), None)
        if trace_stmt is None:
            trace_stmt = TraceStatement(frame, stmt_node)
            self.traced_statements[id(stmt_node)] = trace_stmt

        if nbs().trace_messages_enabled:
            codeline = astunparse.unparse(stmt_node).strip('\n').split('\n')[0]
            codeline = ' ' * getattr(stmt_node, 'col_offset', 0) + codeline
            self.EVENT_LOGGER.warning(' %3d: %10s >>> %s', trace_stmt.lineno, event, codeline)
        if event == TraceEvent.call:
            if trace_stmt.node_id_for_last_call == self.prev_node_id_in_cur_frame:
                if nbs().trace_messages_enabled:
                    self.EVENT_LOGGER.warning(' disable tracing >>>')
                self._disable_tracing()
                return None
            trace_stmt.node_id_for_last_call = self.prev_node_id_in_cur_frame
        self.state_transition_hook(event, trace_stmt, ret_obj)
        return self.sys_tracer


assert not BaseTraceManager._MANAGER_CLASS_REGISTERED
assert TraceManager._MANAGER_CLASS_REGISTERED
