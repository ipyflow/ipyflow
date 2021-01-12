# -*- coding: utf-8 -*-
import ast
import builtins
from contextlib import contextmanager
import itertools
import logging
import sys
from typing import cast, TYPE_CHECKING

import astunparse

from nbsafety.analysis.attr_symbols import AttrSubSymbolChain, GetAttrSubSymbols
from nbsafety.data_model.data_symbol import DataSymbol, DataSymbolType
from nbsafety.data_model.scope import NamespaceScope
from nbsafety.tracing.mutation_event import MutationEvent
from nbsafety.tracing.recovery import on_exception_default_to, return_arg_at_index, return_val
from nbsafety.tracing.trace_events import TraceEvent, EMIT_EVENT
from nbsafety.tracing.trace_stmt import TraceStatement

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
    from types import FrameType
    from nbsafety.data_model.scope import Scope
    from nbsafety.safety import NotebookSafety
    SymbolRef = Union[str, AttrSubSymbolChain]
    AttrSubVal = Union[str, int]
    RecordedArg = Tuple[AttrSubSymbolChain, int]
    RecordedArgs = Set[RecordedArg]
    DeepRef = Tuple[int, Optional[str], Tuple[RecordedArg, ...]]
    Mutation = Tuple[int, Tuple[RecordedArg, ...], MutationEvent]
    RefCandidate = Optional[Tuple[int, int, Optional[str]]]
    DeepRefCandidate = Tuple[RefCandidate, MutationEvent, RecordedArgs]
    SavedStoreData = Tuple[NamespaceScope, Any, AttrSubVal, bool]
    LexicalCallNestingStack = List[Scope]


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
        self.trace_event_counter = 0
        self.prev_event: Optional[TraceEvent] = None
        self.prev_trace_stmt: Optional[TraceStatement] = None
        self.seen_stmts: 'Set[int]' = set()
        self.call_depth = 0
        self.traced_statements: Dict[int, TraceStatement] = {}
        self.tracing_enabled = False
        self.tracing_reset_pending = False

        setattr(builtins, EMIT_EVENT, self._emit_event)

        self._stack: 'List[Tuple[Any, ...]]' = []
        self._stack_item_initializers: 'Dict[str, Callable[[], Any]]' = {}
        self._stack_items_with_manual_initialization: 'Set[str]' = set()
        self._registering_stack_state_context = False
        with self._register_stack_state():
            # everything here should be copyable
            self.prev_trace_stmt_in_cur_frame: Optional[TraceStatement] = None
            self.loaded_data_symbols: Set[DataSymbol] = set()
            self.saved_store_data: List[SavedStoreData] = []
            self.deep_refs: Set[DeepRef] = set()
            self.mutations: Set[Mutation] = set()
            self.deep_ref_candidates: List[DeepRefCandidate] = []
            self.nested_call_stack: LexicalCallNestingStack = []
            self.should_record_args = False
            self.should_record_args_stack: List[bool] = []
            self.literal_namespace: Optional[NamespaceScope] = None
            self.first_obj_id_in_chain: Optional[int] = None
            with self._needing_manual_initialization():
                self.cur_frame_original_scope = safety.global_scope
                self.active_scope = safety.global_scope
                self.inside_lambda = False

    @property
    def _stack_item_names(self):
        return itertools.chain(self._stack_item_initializers.keys(), self._stack_items_with_manual_initialization)

    @contextmanager
    def _register_stack_state(self):
        self._registering_stack_state_context = True
        original_state = set(self.__dict__.keys())
        yield
        self._registering_stack_state_context = False
        stack_item_names = set(self.__dict__.keys() - original_state)
        for stack_item_name in stack_item_names - self._stack_items_with_manual_initialization:
            stack_item = self.__dict__[stack_item_name]
            if stack_item is None:
                self._stack_item_initializers[stack_item_name] = lambda: None
            elif isinstance(stack_item, bool):
                init_val = bool(stack_item)
                self._stack_item_initializers[stack_item_name] = lambda: init_val
            else:
                self._stack_item_initializers[stack_item_name] = type(stack_item)

    @contextmanager
    def _needing_manual_initialization(self):
        assert self._registering_stack_state_context
        original_state = set(self.__dict__.keys())
        yield
        self._stack_items_with_manual_initialization = set(self.__dict__.keys() - original_state)

    @contextmanager
    def _push_stack(self):
        self._stack.append(tuple(self.__dict__[stack_item] for stack_item in self._stack_item_names))
        for stack_item, initializer in self._stack_item_initializers.items():
            self.__dict__[stack_item] = initializer()
        for stack_item in self._stack_items_with_manual_initialization:
            del self.__dict__[stack_item]
        yield
        uninitialized_items = []
        for stack_item in self._stack_items_with_manual_initialization:
            if stack_item not in self.__dict__:
                uninitialized_items.append(stack_item)
        if len(uninitialized_items) > 0:
            raise ValueError(
                "Stack item(s) %s requiring manual initialization were not initialized" % uninitialized_items
            )

    def _handle_call_transition(self, trace_stmt: 'TraceStatement'):
        new_scope = trace_stmt.get_post_call_scope()
        with self._push_stack():
            # TODO: figure out a better way to determine if we're inside a lambda
            #  could this one lead to a false negative if a lambda is in the default of a function def kwarg?
            self.inside_lambda = not isinstance(
                trace_stmt.stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
            self.cur_frame_original_scope = new_scope
            self.active_scope = new_scope
        self.prev_trace_stmt_in_cur_frame = self.prev_trace_stmt = trace_stmt

    def _pop_stack(self):
        for stack_item_name, stack_item in zip(self._stack_item_names, self._stack.pop()):
            self.__dict__[stack_item_name] = stack_item

    def _check_prev_stmt_done_executing_hook(self, event: 'TraceEvent', trace_stmt: 'TraceStatement'):
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

    def _handle_return_transition(self, trace_stmt: 'TraceStatement'):
        inside_lambda = self.inside_lambda
        cur_frame_scope = self.cur_frame_original_scope
        self._pop_stack()
        return_to_stmt = self.prev_trace_stmt_in_cur_frame
        assert return_to_stmt is not None
        if self.prev_event != TraceEvent.exception:
            # exception events are followed by return events until we hit an except clause
            # no need to track dependencies in this case
            if isinstance(return_to_stmt.stmt_node, ast.ClassDef):
                return_to_stmt.class_scope = cast(NamespaceScope, cur_frame_scope)
            elif isinstance(trace_stmt.stmt_node, ast.Return) or inside_lambda:
                if not trace_stmt.lambda_call_point_deps_done_once:
                    trace_stmt.lambda_call_point_deps_done_once = True
                    return_to_stmt.call_point_deps.append(trace_stmt.compute_rval_dependencies())

    def state_transition_hook(
            self,
            event: 'TraceEvent',
            trace_stmt: 'TraceStatement'
    ):
        self.trace_event_counter += 1

        self._check_prev_stmt_done_executing_hook(event, trace_stmt)

        if event == TraceEvent.call:
            self._handle_call_transition(trace_stmt)
        if event == TraceEvent.return_:
            self._handle_return_transition(trace_stmt)
        self.prev_event = event

    def _emit_event(self, evt: str, orig_node_id: int, **kwargs: 'Any'):
        event = TraceEvent(evt)
        if event == TraceEvent.before_stmt:
            return self.before_stmt_tracer(orig_node_id, sys._getframe().f_back)
        elif event == TraceEvent.after_stmt:
            return self.after_stmt_tracer(orig_node_id, sys._getframe().f_back, ret_expr=kwargs.get('ret_expr', None))
        elif event in (TraceEvent.attribute, TraceEvent.subscript):
            return self.attrsub_tracer(
                kwargs['obj'],
                kwargs['attr_or_sub'],
                kwargs['ctx'],
                kwargs['call_context'],
                is_subscript=event == TraceEvent.subscript,
                obj_name=kwargs.get('name', None),
            )
        elif event == TraceEvent.after_attrsub_chain:
            return self.end_tracer(kwargs['obj'], kwargs['call_context'])
        elif event == TraceEvent.argument:
            return self.arg_recorder(kwargs['obj'], self.safety.ast_node_by_id[orig_node_id])
        elif event == TraceEvent.before_arg_list:
            return self.before_argument_list(kwargs['obj'])
        elif event == TraceEvent.after_arg_list:
            return self.after_argument_list(kwargs['obj'], kwargs['is_attrsub'], kwargs['inside_chain'])
        elif event == TraceEvent.before_literal:
            pass
        elif event == TraceEvent.after_literal:
            return self.literal_tracer(kwargs['obj'])
        else:
            raise ValueError('Unsupported event: %s' % event)

    def _get_namespace_for_obj(self, obj: 'Any', obj_name: 'Optional[str]' = None) -> 'NamespaceScope':
        obj_id = id(obj)
        ns = self.safety.namespaces.get(obj_id, None)
        # print('%s attrsub %s of obj %s' % (ctx, attr_or_subscript, obj))
        if ns is not None:
            return ns
        class_scope = self.safety.namespaces.get(id(obj.__class__), None)
        if class_scope is not None:
            # print('found class scope %s containing %s' % (class_scope, list(class_scope.all_data_symbols_this_indentation())))
            ns = class_scope.clone(obj)
            if obj_name is not None:
                ns.scope_name = obj_name
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
            ns = NamespaceScope(obj, self.safety, scope_name, parent_scope=None)
        # FIXME: brittle strategy for determining parent scope of obj
        if ns.parent_scope is None:
            if (
                    obj_name is not None and
                    obj_name not in self.prev_trace_stmt_in_cur_frame.frame.f_locals
            ):
                parent_scope = self.safety.global_scope
            else:
                parent_scope = self.active_scope
            ns.parent_scope = parent_scope
        return ns

    @on_exception_default_to(return_arg_at_index(1, logger))
    def attrsub_tracer(
            self, obj, attr_or_subscript, ctx: str, call_context: bool, is_subscript: bool, obj_name: 'Optional[str]'
    ):
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

            scope = self._get_namespace_for_obj(obj, obj_name=obj_name)
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
                        ((self.trace_event_counter, obj_id, obj_name), mutation_event, set())
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
    def end_tracer(self, obj: 'Any', call_context: bool):
        first_obj_id_in_chain = self.first_obj_id_in_chain
        self.first_obj_id_in_chain = None
        if not self.tracing_enabled:
            return obj
        if self.prev_trace_stmt_in_cur_frame.finished:
            self.active_scope = self.cur_frame_original_scope
            return obj
        if call_context and len(self.deep_ref_candidates) > 0:
            (evt_counter, obj_id, obj_name), mutation_event, recorded_args = self.deep_ref_candidates.pop()
            if evt_counter == self.trace_event_counter:
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
    def arg_recorder(self, arg_obj: 'Any', arg_node: 'ast.AST'):
        if not self.tracing_enabled:
            return arg_obj
        if self.prev_trace_stmt_in_cur_frame.finished or not self.should_record_args:
            return arg_obj
        if not isinstance(arg_node, (ast.Attribute, ast.Subscript, ast.Call, ast.Name)):
            return arg_obj
        if len(self.deep_ref_candidates) == 0:
            logger.error('Error: no associated symbol for recorded args; skipping recording')
            return arg_obj

        arg_obj_id = id(arg_obj)
        # TODO: we should be able to get the actual data symbol during live tracing,
        #  instead of trying to resolve from an attrsub chain determined via analysis
        recorded_arg = GetAttrSubSymbols()(arg_node)
        self.deep_ref_candidates[-1][-1].add((recorded_arg, arg_obj_id))

        return arg_obj

    @on_exception_default_to(return_arg_at_index(1, logger))
    def before_argument_list(self, obj):
        if not self.tracing_enabled:
            return obj
        # if self.prev_trace_stmt.finished:
        #     return obj
        self.nested_call_stack.append(self.active_scope)
        self.active_scope = self.cur_frame_original_scope
        return obj

    @on_exception_default_to(return_arg_at_index(1, logger))
    def after_argument_list(self, obj: 'Any', should_pop_should_record_args_stack: bool, inside_chain: bool):
        if not self.tracing_enabled:
            return obj
        # if self.prev_trace_stmt.finished:
        #     return obj
        self.active_scope = self.nested_call_stack.pop()
        if should_pop_should_record_args_stack:
            self.should_record_args = self.should_record_args_stack.pop()
        if inside_chain:
            # TODO: I don't think any test exercises this atm
            self.active_scope = self._get_namespace_for_obj(obj)
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

    def after_stmt_tracer(self, stmt_id: int, frame: 'FrameType', ret_expr: 'Optional[Any]' = None):
        if stmt_id in self.seen_stmts:
            return ret_expr
        stmt = self.safety.ast_node_by_id.get(stmt_id, None)
        if stmt is not None:
            self._sys_tracer(frame, TraceEvent.after_stmt, stmt)
        return ret_expr

    def before_stmt_tracer(self, stmt_id: int, frame: 'FrameType'):
        if stmt_id in self.seen_stmts:
            return
        # logger.warning('reenable tracing: %s', site_id)
        if self.prev_trace_stmt_in_cur_frame is not None:
            prev_trace_stmt_in_cur_frame = self.prev_trace_stmt_in_cur_frame
            # both of the following stmts should be processed when body is entered
            if isinstance(prev_trace_stmt_in_cur_frame.stmt_node, (ast.For, ast.If, ast.With)):
                self.after_stmt_tracer(prev_trace_stmt_in_cur_frame.stmt_id, frame)
        trace_stmt = self.traced_statements.get(stmt_id, None)
        if trace_stmt is None:
            trace_stmt = TraceStatement(
                self.safety,
                frame,
                cast(ast.stmt, self.safety.ast_node_by_id[stmt_id]),
                self.cur_frame_original_scope
            )
            self.traced_statements[stmt_id] = trace_stmt
        self.prev_trace_stmt_in_cur_frame = trace_stmt
        self.prev_trace_stmt = trace_stmt
        if not self.tracing_enabled:
            assert not self.tracing_reset_pending
            self._enable_tracing()
            self.tracing_reset_pending = True
            _finish_tracing_reset()  # trigger the tracer with a frame

    def after_stmt_reset_hook(self):
        self.loaded_data_symbols.clear()
        self.saved_store_data.clear()
        self.deep_refs.clear()
        self.mutations.clear()
        self.deep_ref_candidates.clear()
        self.nested_call_stack.clear()
        self.should_record_args_stack.clear()
        self.active_scope = self.cur_frame_original_scope
        self.should_record_args = False
        self.literal_namespace = None
        self.first_obj_id_in_chain = None

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
            self._enable_tracing()
            yield
        finally:
            self._disable_tracing(check_enabled=False)

    def _attempt_to_reenable_tracing(self, frame: 'FrameType'):
        assert self.tracing_reset_pending
        self.tracing_reset_pending = False
        call_depth = 0
        while frame is not None:
            if frame.f_code.co_filename.startswith('<ipython-input'):
                call_depth += 1
            frame = frame.f_back
        # put us back in a good self given weird way notebook executes code
        if call_depth == 1 and self.call_depth == 0:
            self.call_depth = 1
        while self.call_depth > call_depth:
            self.call_depth -= 1
            self._stack.pop()
        while len(self.nested_call_stack) > 0:
            self.nested_call_stack.pop()
        if call_depth != self.call_depth:
            # TODO: also check that the stacks agree with each other beyond just size
            # logger.warning('reenable tracing failed: %d vs %d', call_depth, self.call_depth)
            self._disable_tracing()
        # else:
        #     logger.warning('reenable tracing: %d vs %d', call_depth, self.call_depth)
        return None
        # TODO: eventually we'd like to reenable tracing even when the call depth isn't mismatched
        # scopes_to_push = []
        # while frame is not None:
        #     if frame.f_code.co_filename.startswith('<ipython-input'):
        #         call_depth += 1
        #         fun_name = frame.f_code.co_name
        #         if fun_name == '<module>':
        #             if self.call_depth == 0:
        #                 self.call_depth = 1
        #             break
        #         cell_num, lineno = TraceState.get_position(frame)
        #         stmt_node = safety.selfment_cache[cell_num][lineno]
        #         func_cell = self.safety.selfment_to_func_cell[id(stmt_node)]
        #         scopes_to_push.append(func_cell.call_scope)
        #     frame = frame.f_back
        # scopes_to_push.reverse()
        # scopes_to_push = scopes_to_push[self.call_depth-1:]
        # for scope in scopes_to_push:
        #     self.push_stack(scope)
        # self.call_depth = call_depth
        # return None

    @on_exception_default_to(return_val(None, logger))
    def _sys_tracer(self, frame: 'FrameType', evt: 'Union[str, TraceEvent]', extra):
        if isinstance(evt, str):
            event = TraceEvent(evt)
        else:
            event = evt

        if self.tracing_reset_pending:
            assert event == TraceEvent.call
            self._attempt_to_reenable_tracing(frame)

        # notebook cells have filenames that appear as '<ipython-input...>'
        if frame.f_code.co_filename.startswith('<ipython-input'):
            self.safety.maybe_set_name_to_cell_num_mapping(frame)
        else:
            return None

        if event == TraceEvent.line:
            return self._sys_tracer

        if event not in (TraceEvent.return_, TraceEvent.after_stmt) and not self.tracing_enabled:
            return None

        # IPython quirk -- every line in outer scope apparently wrapped in lambda
        # We want to skip the outer 'call' and 'return' for these
        if event == TraceEvent.call:
            self.call_depth += 1
            if self.call_depth == 1:
                return self._sys_tracer

        if event == TraceEvent.return_:
            self.call_depth -= 1
            if self.call_depth == 0:
                return self._sys_tracer

        cell_num, lineno = self.safety.get_position(frame)

        if event == TraceEvent.after_stmt:
            stmt_node = extra
        else:
            try:
                stmt_node = self.safety.statement_cache[cell_num][lineno]
            except KeyError:
                if self.safety.is_develop:
                    logger.warning("got key error for stmt node in cell %d, line %d", cell_num, lineno)
                return self._sys_tracer

        trace_stmt = self.traced_statements.get(id(stmt_node), None)
        if trace_stmt is None:
            trace_stmt = TraceStatement(self.safety, frame, stmt_node, self.cur_frame_original_scope)
            self.traced_statements[id(stmt_node)] = trace_stmt

        if self.safety.config.trace_messages_enabled:
            codeline = astunparse.unparse(stmt_node).strip('\n').split('\n')[0]
            codeline = ' ' * getattr(stmt_node, 'col_offset', 0) + codeline
            logger.warning(' %3d: %9s >>> %s', lineno, event, codeline)
        if event == TraceEvent.call:
            if trace_stmt.call_seen:
                self.call_depth -= 1
                if self.call_depth == 1:
                    self.call_depth = 0
                self._disable_tracing()
                return None
            trace_stmt.call_seen = True
        self.state_transition_hook(event, trace_stmt)
        return self._sys_tracer
