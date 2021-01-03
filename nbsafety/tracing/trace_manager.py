# -*- coding: utf-8 -*-
import ast
import builtins
import logging
import sys
from typing import cast, TYPE_CHECKING

from nbsafety.analysis.attr_symbols import AttrSubSymbolChain
from nbsafety.data_model.data_symbol import DataSymbol, DataSymbolType
from nbsafety.data_model.scope import NamespaceScope
from nbsafety.tracing.mutation_event import MutationEvent
from nbsafety.tracing.hooks import TracingHook
from nbsafety.tracing.recovery import on_exception_default_to, return_arg_at_index
from nbsafety.tracing.sys_tracer import make_sys_tracer
from nbsafety.tracing.trace_events import TraceEvent
from nbsafety.tracing.trace_stmt import TraceStatement

if TYPE_CHECKING:
    from typing import Any, Dict, List, Optional, Set, Tuple, Union
    from nbsafety.data_model.scope import Scope
    from nbsafety.safety import NotebookSafety
    SymbolRef = Union[str, AttrSubSymbolChain]
    AttrSubVal = Union[str, int]
    DeepRef = Tuple[int, Optional[str], Tuple[SymbolRef, ...]]
    Mutation = Tuple[int, Tuple[SymbolRef, ...], MutationEvent]
    RefCandidate = Optional[Tuple[int, int, Optional[str]]]
    RecordedArgs = Set[Tuple[SymbolRef, int]]
    DeepRefCandidate = Tuple[RefCandidate, MutationEvent, RecordedArgs]
    SavedStoreData = Tuple[NamespaceScope, Any, AttrSubVal, bool]
    LexicalCallNestingStack = List[Scope]
    TraceStateStackFrame = Tuple[
        TraceStatement,
        bool,
        List[SavedStoreData],
        Set[DeepRef],
        Set[Mutation],
        List[DeepRefCandidate],
        Scope,
        Scope,
        LexicalCallNestingStack,
        bool,
        List[bool],
        NamespaceScope,
        int,
    ]
    TraceStateStack = List[TraceStateStackFrame]


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


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


class ListLiteral(list):
    pass


class DictLiteral(dict):
    pass


def _make_weakrefable_literal(literal):
    if type(literal) == list:
        return ListLiteral(literal)
    elif type(literal) == dict:
        return DictLiteral(literal)
    else:
        return literal


def _finish_tracing_reset():
    # do nothing; we just want to trigger the newly reenabled tracer with a 'call' event
    pass


class TracingManager(object):
    def __init__(self, safety: 'NotebookSafety'):
        self.safety = safety
        self.tracer = make_sys_tracer(safety)
        self.cur_frame_original_scope = safety.global_scope
        self.active_scope = safety.global_scope
        self.prev_event: Optional[TraceEvent] = None
        self.prev_trace_stmt: Optional[TraceStatement] = None
        self.prev_trace_stmt_in_cur_frame: Optional[TraceStatement] = None
        self.seen_stmts: 'Set[int]' = set()
        self.inside_lambda = False
        self.call_depth = 0
        self.traced_statements: Dict[int, TraceStatement] = {}
        self.stack: TraceStateStack = []
        self.tracing_enabled = False
        self.tracing_reset_pending = False

        self.should_record_args = False
        self._register_tracer_func(TracingHook.attrsub_tracer, self.attrsub_tracer)
        self._register_tracer_func(TracingHook.end_tracer, self.end_tracer)
        self._register_tracer_func(TracingHook.arg_recorder, self.arg_recorder)
        self._register_tracer_func(TracingHook.scope_pusher, self.scope_pusher)
        self._register_tracer_func(TracingHook.scope_popper, self.scope_popper)
        self._register_tracer_func(TracingHook.literal_tracer, self.literal_tracer)
        self._register_tracer_func(TracingHook.before_stmt_tracer, self.before_stmt_tracer)
        self._register_tracer_func(TracingHook.after_stmt_tracer, self.after_stmt_tracer)
        self.loaded_data_symbols: Set[DataSymbol] = set()
        self.saved_store_data: List[SavedStoreData] = []
        self.deep_refs: Set[DeepRef] = set()
        self.mutations: Set[Mutation] = set()
        self.deep_ref_candidates: List[DeepRefCandidate] = []
        self.nested_call_stack: LexicalCallNestingStack = []
        self.should_record_args_stack: List[bool] = []
        self.literal_namespace: Optional[NamespaceScope] = None
        self.first_obj_id_in_chain: Optional[int] = None

    @staticmethod
    def _register_tracer_func(tracing_hook: 'TracingHook', tracer_func):
        setattr(builtins, tracing_hook.value, tracer_func)

    def push_stack(self, trace_stmt: 'TraceStatement'):
        new_scope = trace_stmt.get_post_call_scope()
        self.stack.append((
            self.prev_trace_stmt_in_cur_frame,
            self.inside_lambda,
            self.saved_store_data,
            self.deep_refs,
            self.mutations,
            self.deep_ref_candidates,
            self.active_scope,
            self.cur_frame_original_scope,
            self.nested_call_stack,
            self.should_record_args,
            self.should_record_args_stack,
            self.literal_namespace,
            self.first_obj_id_in_chain,
        ))
        self.prev_trace_stmt_in_cur_frame = None
        self.inside_lambda = not isinstance(trace_stmt.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        # TODO: figure out a better way to determine if we're inside a lambda
        #  could this one lead to a false negative if a lambda is in the default of a function def kwarg?
        self.saved_store_data = []
        self.deep_refs = set()
        self.mutations = set()
        self.deep_ref_candidates = []
        self.cur_frame_original_scope = new_scope
        self.active_scope = new_scope
        self.should_record_args = False
        self.should_record_args_stack = []
        self.nested_call_stack = []
        self.literal_namespace = None
        self.first_obj_id_in_chain = None

    def pop_stack(self):
        (
            self.prev_trace_stmt_in_cur_frame,
            self.inside_lambda,
            self.saved_store_data,
            self.deep_refs,
            self.mutations,
            self.deep_ref_candidates,
            self.active_scope,
            self.cur_frame_original_scope,
            self.nested_call_stack,
            self.should_record_args,
            self.should_record_args_stack,
            self.literal_namespace,
            self.first_obj_id_in_chain,
        ) = self.stack.pop()

    def _check_prev_stmt_done_executing_hook(self, event: 'TraceEvent', trace_stmt: 'TraceStatement'):
        if event == TraceEvent.after_stmt:
            trace_stmt.finished_execution_hook()
        elif event == TraceEvent.return_ and self.prev_event not in (TraceEvent.call, TraceEvent.exception):
            # ensuring prev != call ensures we're not inside of a stmt with multiple calls (such as map w/ lambda)
            if self.prev_trace_stmt is not None:
                self.prev_trace_stmt.finished_execution_hook()
            # prev_overall = self.prev_trace_stmt
            # if prev_overall is not None and prev_overall is not self.stack[-1][0]:
            #     # this condition ensures we're not inside of a stmt with multiple calls (such as map w/ lambda)
            #     prev_overall.finished_execution_hook()

    def _handle_call_transition(self, trace_stmt: 'TraceStatement'):
        self.push_stack(trace_stmt)

    def _handle_return_transition(self, trace_stmt: 'TraceStatement'):
        inside_lambda = self.inside_lambda
        cur_frame_scope = self.cur_frame_original_scope
        self.pop_stack()
        return_to_stmt = self.prev_trace_stmt_in_cur_frame
        assert return_to_stmt is not None
        if self.prev_event != TraceEvent.exception:
            # exception events are followed by return events until we hit an except clause
            # no need to track dependencies in this case
            if isinstance(return_to_stmt.stmt_node, ast.ClassDef):
                return_to_stmt.class_scope = cast('NamespaceScope', cur_frame_scope)
            elif isinstance(trace_stmt.stmt_node, ast.Return) or inside_lambda:
                if not trace_stmt.lambda_call_point_deps_done_once:
                    trace_stmt.lambda_call_point_deps_done_once = True
                    return_to_stmt.call_point_deps.append(trace_stmt.compute_rval_dependencies())

    def state_transition_hook(
            self,
            event: 'TraceEvent',
            trace_stmt: 'TraceStatement'
    ):
        self.safety.trace_event_counter[0] += 1

        self._check_prev_stmt_done_executing_hook(event, trace_stmt)

        if event == TraceEvent.call:
            self._handle_call_transition(trace_stmt)
        if event == TraceEvent.return_:
            self._handle_return_transition(trace_stmt)
        self.prev_event = event

    @staticmethod
    def debug_attribute_tracer(obj, attr, ctx):
        logger.debug('%s attr %s of obj %s', ctx, attr, obj)
        return obj

    @on_exception_default_to(return_arg_at_index(1, logger))
    def attrsub_tracer(self, obj, attr_or_subscript, is_subscript, ctx, call_context, obj_name=None):
        if not self.tracing_enabled:
            return obj
        should_record_args = False
        try:
            if obj is None:
                return None
            obj_id = id(obj)
            if self.first_obj_id_in_chain is None:
                self.first_obj_id_in_chain = obj_id
            if isinstance(attr_or_subscript, tuple):
                if not all(isinstance(v, (str, int)) for v in attr_or_subscript):
                    return obj
            elif not isinstance(attr_or_subscript, (str, int)):
                return obj
            scope = self.safety.namespaces.get(obj_id, None)
            # print('%s attrsub %s of obj %s' % (ctx, attr_or_subscript, obj))
            if scope is None:
                class_scope = self.safety.namespaces.get(id(obj.__class__), None)
                if class_scope is not None and not is_subscript:
                    # print('found class scope %s containing %s' % (class_scope, list(class_scope.all_data_symbols_this_indentation())))
                    scope = class_scope.clone(obj)
                    if obj_name is not None:
                        scope.scope_name = obj_name
                else:
                    # print('no scope for class', obj.__class__)
                    # if self.prev_trace_stmt.finished:
                    #     # avoid creating new scopes if we already did this computation
                    #     self.active_scope = None
                    #     return obj
                    try:
                        scope_name = next(iter(self.safety.aliases.get(obj_id, None))).name if obj_name is None else obj_name
                    except (TypeError, StopIteration):
                        scope_name = '<unknown namespace>'
                    scope = NamespaceScope(obj, self.safety, scope_name, parent_scope=None)
                # FIXME: brittle strategy for determining parent scope of obj
                if scope.parent_scope is None:
                    if (
                        obj_name is not None and
                        obj_name not in self.prev_trace_stmt_in_cur_frame.frame.f_locals
                    ):
                        parent_scope = self.safety.global_scope
                    else:
                        parent_scope = self.active_scope
                    scope.parent_scope = parent_scope

            self.active_scope = scope
            # if scope is None:  # or self.prev_trace_stmt.finished:
            #     if ctx in ('Store', 'AugStore'):
            #         self.active_scope = self.original_active_scope
            #     return obj
            if scope is None or self.prev_trace_stmt_in_cur_frame.finished:
                return obj
            elif ctx in ('Store', 'AugStore') and scope is not None:
                self.saved_store_data.append((scope, obj, attr_or_subscript, is_subscript))
                # reset active scope here
                self.active_scope = self.cur_frame_original_scope
            if ctx == 'Load':
                # save off event counter and obj_id
                # if event counter didn't change when we process the Call retval, and if the
                # retval is None, this is a likely signal that we have a mutation
                # TODO: this strategy won't work if the arguments themselves lead to traced function calls
                # print('looking for', attr_or_subscript)
                data_sym = scope.lookup_data_symbol_by_name_this_indentation(
                    attr_or_subscript, is_subscript=is_subscript
                )
                try:
                    obj_attr_or_sub = self.safety.retrieve_namespace_attr_or_sub(
                        obj, attr_or_subscript, is_subscript
                    )
                    if data_sym is None:
                        symbol_type = DataSymbolType.SUBSCRIPT if is_subscript else DataSymbolType.DEFAULT
                        data_sym = DataSymbol(
                            attr_or_subscript,
                            symbol_type,
                            obj_attr_or_sub,
                            scope,
                            self.safety,
                            stmt_node=None,
                            parents=None,
                            refresh_cached_obj=True,
                            implicit=True,
                        )
                        # this is to prevent refs to the scope object from being considered as stale if we just load it
                        data_sym.defined_cell_num = data_sym.required_cell_num = scope.max_defined_timestamp
                        scope.put(attr_or_subscript, data_sym)
                        # print('put', data_sym, 'in', scope.full_namespace_path)
                        # FIXME: DataSymbols should probably register themselves with the alias manager at creation
                        self.safety.aliases[id(obj_attr_or_sub)].add(data_sym)
                    elif data_sym.obj_id != id(obj_attr_or_sub):
                        data_sym.update_obj_ref(obj_attr_or_sub)
                except:
                    pass
                if call_context:
                    should_record_args = True
                    mutation_event = MutationEvent.normal
                    if isinstance(obj, list) and attr_or_subscript == 'append':
                        mutation_event = MutationEvent.list_append
                    self.deep_ref_candidates.append(
                        ((self.safety.trace_event_counter[0], obj_id, obj_name), mutation_event, set())
                    )
                elif data_sym is not None:
                    # TODO: if we have a.b.c, will this consider a.b loaded as well as a.b.c? This is bad if so.
                    self.loaded_data_symbols.add(data_sym)
            return obj
        finally:
            if call_context:
                self.should_record_args_stack.append(self.should_record_args)
                self.should_record_args = should_record_args

    @on_exception_default_to(return_arg_at_index(1, logger))
    def end_tracer(self, obj, call_context):
        first_obj_id_in_chain = self.first_obj_id_in_chain
        self.first_obj_id_in_chain = None
        if not self.tracing_enabled:
            return obj
        if self.prev_trace_stmt_in_cur_frame.finished:
            self.active_scope = self.cur_frame_original_scope
            return obj
        if call_context and len(self.deep_ref_candidates) > 0:
            (evt_counter, obj_id, obj_name), mutation_event, recorded_args = self.deep_ref_candidates.pop()
            if evt_counter == self.safety.trace_event_counter[0]:
                if obj is None:
                    if mutation_event == MutationEvent.normal:
                        try:
                            top_level_sym = next(iter(self.safety.aliases[first_obj_id_in_chain]))
                            if top_level_sym.is_import and top_level_sym.name not in ARG_MUTATION_EXCEPTED_MODULES:
                                # TODO: should it be the other way around? i.e. allow-list for arg mutations, starting
                                #  with np.random.seed?
                                for recorded_arg, _ in recorded_args:
                                    if len(recorded_arg.symbols) > 0:
                                        # only make this an arg mutation event if it looks like there's an arg to mutate
                                        mutation_event = MutationEvent.arg_mutate
                                        break
                        except:
                            pass
                    self.mutations.add((obj_id, tuple(recorded_args), mutation_event))
                else:
                    self.deep_refs.add((obj_id, obj_name, tuple(recorded_args)))
        # print('reset active scope from', self.active_scope, 'to', self.original_active_scope)
        self.active_scope = self.cur_frame_original_scope
        return obj

    @on_exception_default_to(return_arg_at_index(1, logger))
    def arg_recorder(self, arg_obj, name):
        if not self.tracing_enabled:
            return arg_obj
        if self.prev_trace_stmt_in_cur_frame.finished or not self.should_record_args:
            return arg_obj
        if len(self.deep_ref_candidates) == 0:
            logger.error('Error: no associated symbol for recorded args; skipping recording')
            return arg_obj

        arg_obj_id = id(arg_obj)
        recorded_arg = AttrSubSymbolChain(name)
        self.deep_ref_candidates[-1][-1].add((recorded_arg, arg_obj_id))

        return arg_obj

    @on_exception_default_to(return_arg_at_index(1, logger))
    def scope_pusher(self, obj):
        if not self.tracing_enabled:
            return obj
        # if self.prev_trace_stmt.finished:
        #     return obj
        self.nested_call_stack.append(self.active_scope)
        self.active_scope = self.cur_frame_original_scope
        return obj

    @on_exception_default_to(return_arg_at_index(1, logger))
    def scope_popper(self, obj, should_pop_should_record_args_stack):
        if not self.tracing_enabled:
            return obj
        # if self.prev_trace_stmt.finished:
        #     return obj
        self.active_scope = self.nested_call_stack.pop()
        if should_pop_should_record_args_stack:
            self.should_record_args = self.should_record_args_stack.pop()
        return obj

    @on_exception_default_to(return_arg_at_index(1, logger))
    def literal_tracer(self, literal):
        literal = _make_weakrefable_literal(literal)
        if not self.tracing_enabled:
            return literal
        if self.prev_trace_stmt_in_cur_frame.finished:
            return literal
        if isinstance(literal, (dict, list, tuple)):
            scope = NamespaceScope(
                literal, self.safety, None, self.prev_trace_stmt_in_cur_frame.scope
            )
            gen = literal.items() if isinstance(literal, dict) else enumerate(literal)
            for i, obj in gen:
                scope.upsert_data_symbol_for_name(
                    i, obj, set(), self.prev_trace_stmt_in_cur_frame.stmt_node, True
                )
            self.literal_namespace = scope
        return literal

    def after_stmt_tracer(self, stmt_id, frame=None):
        if stmt_id in self.seen_stmts:
            return
        stmt = self.safety.stmt_by_id.get(stmt_id, None)
        if stmt is not None:
            self.tracer(frame or sys._getframe().f_back, TraceEvent.after_stmt, stmt)

    def before_stmt_tracer(self, stmt_id):
        if stmt_id in self.seen_stmts:
            return
        # logger.warning('reenable tracing: %s', site_id)
        if self.prev_trace_stmt_in_cur_frame is not None:
            prev_trace_stmt_in_cur_frame = self.prev_trace_stmt_in_cur_frame
            # both of the following stmts should be processed when body is entered
            if isinstance(prev_trace_stmt_in_cur_frame.stmt_node, (ast.For, ast.If, ast.With)):
                self.after_stmt_tracer(prev_trace_stmt_in_cur_frame.stmt_id, frame=sys._getframe().f_back)
        trace_stmt = self.traced_statements.get(stmt_id, None)
        if trace_stmt is None:
            trace_stmt = TraceStatement(
                self.safety, sys._getframe().f_back, self.safety.stmt_by_id[stmt_id], self.cur_frame_original_scope
            )
            self.traced_statements[stmt_id] = trace_stmt
        self.prev_trace_stmt_in_cur_frame = trace_stmt
        self.prev_trace_stmt = trace_stmt
        if not self.tracing_enabled:
            assert not self.tracing_reset_pending
            self.enable_tracing()
            self.tracing_reset_pending = True
            _finish_tracing_reset()  # trigger the tracer with a frame

    def reset(self):
        self.loaded_data_symbols = set()
        self.saved_store_data = []
        self.deep_refs = set()
        self.mutations = set()
        self.deep_ref_candidates = []
        self.active_scope = self.cur_frame_original_scope
        self.should_record_args = False
        self.literal_namespace = None
        self.first_obj_id_in_chain = None
        # self.nested_call_stack = []
        # self.stmt_transition_hook()

    def enable_tracing(self):
        assert not self.tracing_enabled
        self.tracing_enabled = True
        sys.settrace(self.tracer)

    def disable_tracing(self, check_enabled=True):
        if check_enabled:
            assert self.tracing_enabled
        self.tracing_enabled = False
        sys.settrace(None)
