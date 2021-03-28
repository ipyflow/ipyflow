# -*- coding: future_annotations -*-
import ast
import builtins
from collections import defaultdict
from contextlib import contextmanager
import logging
import sys
from typing import cast, TYPE_CHECKING

import astunparse

from nbsafety.analysis.symbol_edges import get_symbol_rvals
from nbsafety.data_model.data_symbol import DataSymbol, DataSymbolType
from nbsafety.data_model.scope import Scope, NamespaceScope
from nbsafety import singletons
from nbsafety.run_mode import SafetyRunMode
from nbsafety.singletons import nbs
from nbsafety.tracing.mutation_event import MutationEvent
from nbsafety.tracing.trace_events import TraceEvent, EMIT_EVENT
from nbsafety.tracing.trace_stack import TraceStack
from nbsafety.tracing.trace_stmt import TraceStatement

if TYPE_CHECKING:
    from typing import Any, Callable, DefaultDict, Dict, List, Optional, Set, Tuple, Type, Union
    from types import FrameType
    AttrSubVal = Union[str, int]
    NodeId = int
    ObjId = int
    MutationCandidate = Tuple[Tuple[int, ObjId, Optional[str]], MutationEvent, Set[DataSymbol]]
    Mutation = Tuple[int, MutationEvent, Set[DataSymbol]]
    SavedStoreData = Tuple[NamespaceScope, Any, AttrSubVal, bool]
    SavedComplexSymbolLoadData = Tuple[NamespaceScope, Tuple[AttrSubVal, bool]]


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


def _match_literal_namespace_with_literal_elts(literal_obj, literal_node):
    if isinstance(literal_obj, dict):
        gen = literal_obj.items()
        assert isinstance(literal_node, ast.Dict)
        yield from zip(gen, zip(literal_node.keys, literal_node.values))
        return
    elts = literal_node.elts
    cur_node = None
    cur_elt_idx = -1
    for i, obj in enumerate(literal_obj):
        if not isinstance(cur_node, ast.Starred) or len(elts) - cur_elt_idx - 1 >= len(literal_obj) - i:
            cur_elt_idx += 1
            cur_node = elts[cur_elt_idx]
        yield (i, obj), (None, cur_node)



def _finish_tracing_reset():
    # do nothing; we just want to trigger the newly reenabled tracer with a 'call' event
    pass


class BaseTraceManager(singletons.TraceManager):

    _MANAGER_CLASS_REGISTERED = False
    EVENT_HANDLERS_PENDING_REGISTRATION: DefaultDict[TraceEvent, List[Callable[..., Any]]] = defaultdict(list)
    EVENT_HANDLERS_BY_CLASS: Dict[Type['BaseTraceManager'], DefaultDict[TraceEvent, List[Callable[..., Any]]]] = {}

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
                    logger.error('Exception occurred: %s' % exc)
                new_ret = None
            if new_ret is not None:
                kwargs['ret'] = new_ret
        return kwargs.get('ret', None)

    def _make_stack(self):
        return TraceStack(self)

    def _enable_tracing(self):
        assert not self.tracing_enabled
        self.tracing_enabled = True
        sys.settrace(self._sys_tracer)

    def _disable_tracing(self, check_enabled=True):
        if check_enabled:
            assert self.tracing_enabled
        self.tracing_enabled = False
        sys.settrace(None)

    @contextmanager
    def tracing_context(self):
        try:
            setattr(builtins, EMIT_EVENT, self._emit_event)
            self._enable_tracing()
            yield
        finally:
            delattr(builtins, EMIT_EVENT)
            self._disable_tracing(check_enabled=False)

    def _attempt_to_reenable_tracing(self, frame: FrameType):
        return NotImplemented

    def _sys_tracer(self, frame: FrameType, evt: str, *_, **__):
        if self.tracing_reset_pending:
            assert evt == 'call', 'expected call; got event %s' % evt
            self._attempt_to_reenable_tracing(frame)
            return None
        # notebook cells have filenames that appear as '<ipython-input...>'
        if evt == 'line' or not frame.f_code.co_filename.startswith('<ipython-input'):
            return None

        return self._emit_event(evt, 0, _frame=frame)


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
        self.node_id_to_loaded_symbol: Dict[NodeId, DataSymbol] = {}
        self.node_id_to_saved_store_data: Dict[NodeId, SavedStoreData] = {}
        self.node_id_to_loaded_literal_scope: Dict[NodeId, NamespaceScope] = {}
        self.node_id_to_saved_dict_key: Dict[NodeId, Any] = {}

        self.call_stack: TraceStack = self._make_stack()
        with self.call_stack.register_stack_state():
            # everything here should be copyable
            self.prev_trace_stmt_in_cur_frame: Optional[TraceStatement] = None
            self.prev_node_id_in_cur_frame: Optional[NodeId] = None
            self.mutations: List[Mutation] = []
            self.mutation_candidates: List[MutationCandidate] = []

            with self.call_stack.needing_manual_initialization():
                self.cur_frame_original_scope: Scope = nbs().global_scope
                self.active_scope: Scope = nbs().global_scope
                self.inside_lambda = False

            self.lexical_call_stack: TraceStack = self._make_stack()
            with self.lexical_call_stack.register_stack_state():
                self.sym_for_obj_calling_method: Optional[DataSymbol] = None
                self.first_obj_id_in_chain: Optional[ObjId] = None
                self.top_level_node_id_for_chain: Optional[NodeId] = None
                self.saved_complex_symbol_load_data: Optional[SavedComplexSymbolLoadData] = None

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
        self.node_id_to_loaded_symbol.clear()  # TODO: keep this around?
        self.node_id_to_saved_store_data.clear()
        self.node_id_to_loaded_literal_scope.clear()
        self.node_id_to_saved_dict_key.clear()

    def _handle_call_transition(self, trace_stmt: TraceStatement):
        new_scope = trace_stmt.get_post_call_scope()
        with self.call_stack.push():
            # TODO: figure out a better way to determine if we're inside a lambda
            #  could this one lead to a false negative if a lambda is in the default of a function def kwarg?
            self.inside_lambda = not isinstance(
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

    def _handle_return_transition(self, trace_stmt: TraceStatement):
        try:
            inside_lambda = self.inside_lambda
            return_to_stmt: TraceStatement = self.call_stack.get_field('prev_trace_stmt_in_cur_frame')
            assert return_to_stmt is not None
            if self.prev_event != TraceEvent.exception:
                # exception events are followed by return events until we hit an except clause
                # no need to track dependencies in this case
                if isinstance(return_to_stmt.stmt_node, ast.ClassDef):
                    return_to_stmt.class_scope = cast(NamespaceScope, self.cur_frame_original_scope)
                elif isinstance(trace_stmt.stmt_node, ast.Return) or inside_lambda:
                    if not trace_stmt.lambda_call_point_deps_done_once:
                        trace_stmt.lambda_call_point_deps_done_once = True
                        return_to_stmt.call_point_deps.append(trace_stmt.compute_rval_dependencies())
        finally:
            self.call_stack.pop()

    def state_transition_hook(
        self,
        event: TraceEvent,
        trace_stmt: TraceStatement
    ):
        self.trace_event_counter += 1

        self._check_prev_stmt_done_executing_hook(event, trace_stmt)

        if event == TraceEvent.call:
            self._handle_call_transition(trace_stmt)
        if event == TraceEvent.return_:
            self._handle_return_transition(trace_stmt)
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
    ) -> Tuple[Scope, Union[str, int], Any, bool]:
        target = self._partial_resolve_ref(target)
        if isinstance(target, str):
            obj = frame.f_locals[target]
            return self.cur_frame_original_scope, target, obj, False
        else:
            (
                scope, obj, attr_or_sub, is_subscript
            ) = self.node_id_to_saved_store_data[target]
            attr_or_sub_obj = nbs().retrieve_namespace_attr_or_sub(obj, attr_or_sub, is_subscript)
            scope_to_use = scope.get_earliest_ancestor_containing(id(attr_or_sub_obj), is_subscript)
            if scope_to_use is None:
                # Nobody before `scope` has it, so we'll insert it at this level
                scope_to_use = scope
            return scope_to_use, attr_or_sub, attr_or_sub_obj, is_subscript

    def resolve_loaded_symbol(self, symbol_ref: Union[str, int, ast.AST]) -> Optional[DataSymbol]:
        symbol_ref = self._partial_resolve_ref(symbol_ref)
        if isinstance(symbol_ref, int):
            return self.node_id_to_loaded_symbol.get(symbol_ref, None)
        elif isinstance(symbol_ref, str):
            return self.cur_frame_original_scope.lookup_data_symbol_by_name(symbol_ref)
        else:
            return None

    def resolve_symbols(self, symbol_refs: Set[Union[str, int]]) -> Set[DataSymbol]:
        data_symbols = set()
        for ref in symbol_refs:
            maybe_dsym = self.resolve_loaded_symbol(ref)
            if maybe_dsym is not None:
                data_symbols.add(maybe_dsym)
        return data_symbols

    def _get_namespace_for_obj(self, obj: Any, obj_name: Optional[str] = None) -> NamespaceScope:
        obj_id = id(obj)
        ns = nbs().namespaces.get(obj_id, None)
        if ns is not None:
            return ns
        class_scope = nbs().namespaces.get(id(obj.__class__), None)
        if class_scope is not None:
            # logger.info(
            #     'found class scope %s containing %s',
            #     class_scope, list(class_scope.all_data_symbols_this_indentation())
            # )
            ns = class_scope.clone(obj)
            if obj_name is not None:
                ns.scope_name = obj_name
        else:
            # print('no scope for class', obj.__class__)
            try:
                scope_name = next(iter(nbs().aliases.get(obj_id, None))).name if obj_name is None else obj_name
            except (TypeError, StopIteration):
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
        scope, (attr_or_subscript, is_subscript) = self.saved_complex_symbol_load_data
        self.saved_complex_symbol_load_data = None
        data_sym = scope.lookup_data_symbol_by_name_this_indentation(
            attr_or_subscript, is_subscript=is_subscript
        )
        if data_sym is None:
            symbol_type = DataSymbolType.SUBSCRIPT if is_subscript else DataSymbolType.DEFAULT
            data_sym = DataSymbol.create_implicit(attr_or_subscript, obj_attr_or_sub, scope, symbol_type=symbol_type)
        elif data_sym.obj_id != id(obj_attr_or_sub):
            data_sym.update_obj_ref(obj_attr_or_sub)
        return data_sym

    @register_universal_handler
    def _save_node_id(self, _obj, node_id: NodeId, *_, **__):
        self.prev_node_id_in_cur_frame = node_id

    @register_handler((TraceEvent.attribute, TraceEvent.subscript))
    def attrsub_tracer(
        self,
        obj: Any,
        _node_id: NodeId,
        _frame_: FrameType,
        event: TraceEvent,
        *_,
        attr_or_subscript,
        ctx: str,
        call_context: bool,
        top_level_node_id: NodeId,
        obj_name: Optional[str] = None,
        **__
    ):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return
        if obj is None:
            return
        logger.info('%s attrsub %s of obj %s', ctx, attr_or_subscript, obj)
        sym_for_obj = self._clear_info_and_maybe_lookup_or_create_complex_symbol(obj)
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
        if ctx != 'Load':
            assert ctx in ('Store', 'AugStore')
            logger.info("save store data for node id %d", top_level_node_id)
            self.node_id_to_saved_store_data[top_level_node_id] = (scope, obj, attr_or_subscript, is_subscript)
            return
        if call_context:
            mutation_event = MutationEvent.normal
            if isinstance(obj, list) and attr_or_subscript == 'append':
                mutation_event = MutationEvent.list_append
            # save off event counter and obj_id
            # if event counter didn't change when we process the Call retval, and if the
            # retval is None, this is a likely signal that we have a mutation
            # TODO: this strategy won't work if the arguments themselves lead to traced function calls
            #  to cope, put DeepRefCandidates (or equivalent) in the lexical stack?
            self.mutation_candidates.append(
                ((self.trace_event_counter, obj_id, obj_name), mutation_event, set())
            )
            if not is_subscript:
                if sym_for_obj is None and obj_name is not None:
                    sym_for_obj = self.cur_frame_original_scope.lookup_data_symbol_by_name(obj_name)
                if sym_for_obj is None:
                    if self.prev_trace_stmt_in_cur_frame is not None:
                        sym_for_obj = DataSymbol.create_implicit(obj_name, obj, self.cur_frame_original_scope)
                        # sym_for_obj = self.cur_frame_original_scope.upsert_data_symbol_for_name(
                        #     obj_name, obj, set(), self.prev_trace_stmt_in_cur_frame.stmt_node, False
                        # )
                if sym_for_obj is not None:
                    self.sym_for_obj_calling_method = sym_for_obj
        else:
            self.saved_complex_symbol_load_data = (scope, (attr_or_subscript, is_subscript))

    @register_handler(TraceEvent.after_complex_symbol)
    def after_complex_symbol(self, obj: Any, *_, call_context: bool, ctx: str, **__):
        try:
            if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
                return
            if self.first_obj_id_in_chain is None:
                return
            if ctx == 'Store':
                # don't trace after events w/ store context
                return
            loaded_sym = self._clear_info_and_maybe_lookup_or_create_complex_symbol(obj)
            if call_context and len(self.mutation_candidates) > 0:
                (evt_counter, obj_id, obj_name), mutation_event, recorded_args = self.mutation_candidates.pop()
                if evt_counter == self.trace_event_counter:
                    if obj is None:
                        if mutation_event == MutationEvent.normal:
                            try:
                                top_level_sym = next(iter(nbs().aliases[self.first_obj_id_in_chain]))
                                if top_level_sym.is_import and top_level_sym.name not in ARG_MUTATION_EXCEPTED_MODULES:
                                    # TODO: should it be the other way around?
                                    #  i.e. allow-list for arg mutations, starting with np.random.seed?
                                    if len(recorded_args) > 0:
                                        # only make this an arg mutation event if it looks like there's an arg to mutate
                                        mutation_event = MutationEvent.arg_mutate
                            except:
                                pass
                        self.mutations.append((obj_id, mutation_event, recorded_args))
                    else:
                        if self.sym_for_obj_calling_method is not None:
                            loaded_sym = self.sym_for_obj_calling_method
                            self.sym_for_obj_calling_method = None
            if loaded_sym is not None:
                self.node_id_to_loaded_symbol[self.top_level_node_id_for_chain] = loaded_sym
        finally:
            self.saved_complex_symbol_load_data = None
            self.first_obj_id_in_chain = None
            self.top_level_node_id_for_chain = None
            self.sym_for_obj_calling_method = None
            self.active_scope = self.cur_frame_original_scope

    @register_handler(TraceEvent.argument)
    def arg_recorder(self, arg_obj: Any, arg_node_id: int, *_, **__):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return
        arg_node = nbs().ast_node_by_id.get(arg_node_id, None)
        if not isinstance(arg_node, (ast.Attribute, ast.Subscript, ast.Call, ast.Name)):
            return
        if len(self.mutation_candidates) == 0:
            return

        if isinstance(arg_node, ast.Name):
            assert self.active_scope is self.cur_frame_original_scope
            arg_dsym = self.active_scope.lookup_data_symbol_by_name(arg_node.id)
            if arg_dsym is None:
                arg_dsym = DataSymbol.create_implicit(arg_node.id, arg_obj, self.active_scope)
        else:
            arg_dsym = self.node_id_to_loaded_symbol.get(arg_node_id, None)
        if arg_dsym is not None:
            self.mutation_candidates[-1][-1].add(arg_dsym)

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

    @register_handler(TraceEvent.before_literal)
    def before_literal(self, *_, **__):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return
        parent_scope = self.active_literal_scope or self.cur_frame_original_scope
        with self.lexical_literal_stack.push():
            self.active_literal_scope = NamespaceScope(None, NamespaceScope.ANONYMOUS, parent_scope)

    @register_handler(TraceEvent.after_literal)
    def after_literal(self, literal: Any, node_id: NodeId, *_, **__):
        if not self.tracing_enabled or self.prev_trace_stmt_in_cur_frame.finished:
            return literal
        try:
            self.active_literal_scope.update_obj_ref(literal)
            starred_idx = -1
            for (i, inner_obj), (inner_key_node, inner_val_node) in _match_literal_namespace_with_literal_elts(literal, nbs().ast_node_by_id[node_id]):
                # TODO: memoize symbol resolution; otherwise this will be quadratic for deeply nested literals
                if isinstance(inner_val_node, ast.Starred):
                    inner_symbols = set()
                    starred_idx += 1
                    starred_sym = self.resolve_loaded_symbol(inner_val_node)
                    starred_namespace = None if starred_sym is None else nbs().namespaces.get(starred_sym.obj_id, None)
                    if starred_namespace is not None:
                        starred_dep = starred_namespace.lookup_data_symbol_by_name_this_indentation(starred_idx, is_subscript=True)
                        inner_symbols.add(starred_dep)
                else:
                    inner_symbols = self.resolve_symbols(get_symbol_rvals(inner_val_node))
                    if inner_key_node is not None:
                        inner_symbols.add(self.resolve_loaded_symbol(inner_key_node))
                inner_symbols.discard(None)
                if isinstance(i, (int, str)):
                    self.active_literal_scope.upsert_data_symbol_for_name(
                        i, inner_obj, inner_symbols, self.prev_trace_stmt_in_cur_frame.stmt_node, True
                    )
            self.node_id_to_loaded_literal_scope[node_id] = self.active_literal_scope
            parent_scope: Scope = self.active_literal_scope.parent_scope
            assert parent_scope is not None
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
            if frame.f_code.co_filename.startswith('<ipython-input'):
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
        if nbs().settings.trace_messages_enabled:
            self.EVENT_LOGGER.warning('reenable tracing >>>')

    @register_handler((TraceEvent.call, TraceEvent.return_, TraceEvent.exception))
    def handle_sys_events(
        self,
        _ret: None,
        _node_id: int,
        frame: FrameType,
        event: TraceEvent,
        *_,
        stmt_node: Optional[ast.stmt] = None,
        **__
    ):
        # right now, this should only be enabled for notebook code
        assert frame.f_code.co_filename.startswith('<ipython-input')
        assert self.tracing_enabled or event == TraceEvent.after_stmt
        nbs().maybe_set_name_to_cell_num_mapping(frame)

        # IPython quirk -- every line in outer scope apparently wrapped in lambda
        # We want to skip the outer 'call' and 'return' for these
        if event == TraceEvent.call:
            self.call_depth += 1
            if self.call_depth == 1:
                return self._sys_tracer

        if event == TraceEvent.return_:
            self.call_depth -= 1
            if self.call_depth == 0:
                return

        cell_num, lineno = nbs().get_position(frame)

        if event == TraceEvent.after_stmt:
            assert stmt_node is not None
        else:
            try:
                stmt_node = nbs().statement_cache[cell_num][lineno]
            except KeyError as e:
                self.EVENT_LOGGER.warning("got key error for stmt node in cell %d, line %d", cell_num, lineno)
                if nbs().is_develop:
                    raise e
                return self._sys_tracer

        trace_stmt = self.traced_statements.get(id(stmt_node), None)
        if trace_stmt is None:
            trace_stmt = TraceStatement(frame, stmt_node)
            self.traced_statements[id(stmt_node)] = trace_stmt

        if nbs().settings.trace_messages_enabled:
            codeline = astunparse.unparse(stmt_node).strip('\n').split('\n')[0]
            codeline = ' ' * getattr(stmt_node, 'col_offset', 0) + codeline
            self.EVENT_LOGGER.warning(' %3d: %10s >>> %s', trace_stmt.lineno, event, codeline)
        if event == TraceEvent.call:
            if trace_stmt.call_seen:
                if nbs().settings.trace_messages_enabled:
                    self.EVENT_LOGGER.warning(' disable tracing >>>')
                self._disable_tracing()
                return None
            trace_stmt.call_seen = True
        self.state_transition_hook(event, trace_stmt)
        return self._sys_tracer


assert not BaseTraceManager._MANAGER_CLASS_REGISTERED
assert TraceManager._MANAGER_CLASS_REGISTERED
