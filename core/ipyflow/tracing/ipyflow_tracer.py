# -*- coding: utf-8 -*-
import ast
import logging
import symtable
import sys
from collections import defaultdict
from contextlib import contextmanager
from types import FrameType, ModuleType
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Generator,
    List,
    Optional,
    Set,
    Tuple,
    Union,
    cast,
)

import pyccolo as pyc
from IPython import get_ipython

from ipyflow.analysis.live_refs import compute_live_dead_symbol_refs
from ipyflow.annotations.compiler import compile_and_register_handlers_for_module
from ipyflow.api.lift import code as api_code
from ipyflow.api.lift import deps as api_deps
from ipyflow.api.lift import lift as api_lift
from ipyflow.api.lift import rdeps as api_rdeps
from ipyflow.api.lift import rusers as api_rusers
from ipyflow.api.lift import timestamp as api_timestamp
from ipyflow.api.lift import users as api_users
from ipyflow.api.lift import watchpoints as api_watchpoints
from ipyflow.data_model.code_cell import cells
from ipyflow.data_model.data_symbol import DataSymbol
from ipyflow.data_model.namespace import Namespace
from ipyflow.data_model.scope import Scope
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.singletons import SingletonBaseTracer, flow
from ipyflow.tracing.external_calls import resolve_external_call
from ipyflow.tracing.external_calls.base_handlers import ExternalCallHandler
from ipyflow.tracing.flow_ast_rewriter import DataflowAstRewriter
from ipyflow.tracing.symbol_resolver import resolve_rval_symbols
from ipyflow.tracing.trace_stmt import TraceStatement
from ipyflow.tracing.utils import match_container_obj_or_namespace_with_literal_nodes
from ipyflow.types import SupportedIndexType

if TYPE_CHECKING:
    import astunparse
elif hasattr(ast, "unparse"):
    astunparse = ast
else:
    import astunparse

AttrSubVal = SupportedIndexType
NodeId = int
ObjId = int
SavedStoreData = Tuple[Namespace, Any, AttrSubVal, bool]
SavedDelData = Tuple[Namespace, Any, AttrSubVal, bool]
SavedComplexSymbolLoadData = Tuple[Namespace, Any, AttrSubVal, bool, Optional[str]]


logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


reactive_spec = pyc.AugmentationSpec(
    aug_type=pyc.AugmentationType.prefix, token="$", replacement=""
)
cascading_reactive_spec = pyc.AugmentationSpec(
    aug_type=pyc.AugmentationType.prefix, token="$$", replacement=""
)
blocking_spec = pyc.AugmentationSpec(
    aug_type=pyc.AugmentationType.prefix, token="$:", replacement=""
)


class ModuleIniter(pyc.BaseTracer):
    @pyc.register_raw_handler(pyc.init_module)
    def init_cell(self, _obj, _node_id, frame: FrameType, *_, **__):
        flow().set_name_to_cell_num_mapping(frame)
        for tracer in pyc._TRACER_STACK:
            tracer._tracing_enabled_files.add(frame.f_code.co_filename)

    @property
    def should_patch_meta_path(self) -> bool:
        return False


class StackFrameManager(SingletonBaseTracer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.call_depth = 0
        self.external_call_depth = 0

    @pyc.register_raw_handler((pyc.call, pyc.return_))
    def handle_first_ipython_frame(
        self,
        _ret: Any,
        _node_id: None,
        frame: FrameType,
        event: pyc.TraceEvent,
        *_,
        **__,
    ):
        if frame.f_code.co_name == "<traced_lambda>":
            return pyc.SkipAll
        # IPython quirk -- every line in outer scope apparently wrapped in lambda
        # We want to skip the outer 'call' and 'return' for these
        if event == pyc.call:
            self.call_depth += 1
            self.external_call_depth += not flow().is_cell_file(
                frame.f_code.co_filename
            )
            if self.call_depth == 1:
                return pyc.SkipAll
        elif event == pyc.return_:
            self.call_depth -= 1
            self.external_call_depth -= not flow().is_cell_file(
                frame.f_code.co_filename
            )
            if flow().is_dev_mode:
                assert self.call_depth >= 0
            if self.call_depth == 0:
                return pyc.SkipAll

    @property
    def should_patch_meta_path(self) -> bool:
        return False


class DataflowTracer(StackFrameManager):
    ast_rewriter_cls = DataflowAstRewriter

    def should_propagate_handler_exception(
        self, evt: pyc.TraceEvent, exc: Exception
    ) -> bool:
        return flow().is_dev_mode

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._tracing_enabled_files.discard(self.defined_file)
        with self.persistent_fields():
            self.reactive_node_ids: Set[int] = self.augmented_node_ids_by_spec[
                reactive_spec
            ]
            self.cascading_reactive_node_ids: Set[
                int
            ] = self.augmented_node_ids_by_spec[cascading_reactive_spec]
            self.blocking_node_ids: Set[int] = self.augmented_node_ids_by_spec[
                blocking_spec
            ]
        self.tracing_disabled_since_last_module_stmt = False
        self._module_stmt_counter = 0
        self._saved_stmt_ret_expr: Optional[Any] = None
        self._seen_loop_ids: Set[NodeId] = set()
        self._seen_functions_ids: Set[NodeId] = set()
        self.prev_event: Optional[pyc.TraceEvent] = None
        self.prev_trace_stmt: Optional[TraceStatement] = None
        self.seen_stmts: Set[NodeId] = set()
        self.traced_statements: Dict[NodeId, TraceStatement] = {}
        self.node_id_to_loaded_symbols: Dict[NodeId, List[DataSymbol]] = {}
        self.node_id_to_saved_store_data: Dict[NodeId, SavedStoreData] = {}
        self.node_id_to_saved_live_subscript_refs: Dict[NodeId, Set[DataSymbol]] = {}
        self.node_id_to_saved_del_data: Dict[NodeId, SavedDelData] = {}
        self.node_id_to_loaded_literal_scope: Dict[NodeId, Namespace] = {}
        self.node_id_to_saved_dict_key: Dict[NodeId, Any] = {}
        self.this_stmt_updated_symbols: Set[DataSymbol] = set()
        self.pending_usage_updates_by_sym: Dict[DataSymbol, bool] = {}
        self.cur_cell_symtab: Optional[symtable.SymbolTable] = None

        self.calling_symbol: Optional[DataSymbol] = None
        self.call_stack: pyc.TraceStack = self.make_stack()
        with self.call_stack.register_stack_state():
            # everything here should be copyable
            self.prev_trace_stmt_in_cur_frame: Optional[TraceStatement] = None
            self.prev_node_id_in_cur_frame: Optional[NodeId] = None
            self.external_calls: List[ExternalCallHandler] = []
            self.is_external_call_pending_return: bool = False
            self.saved_assign_rhs_obj: Optional[Any] = None
            # this one gets set regardless of whether tracing enabled
            self.next_stmt_node_id: Optional[NodeId] = None

            self.pending_class_namespaces: List[Namespace] = []

            with self.call_stack.needing_manual_initialization():
                self.cur_frame_original_scope: Scope = flow().global_scope
                self.active_scope: Scope = flow().global_scope
                self.inside_anonymous_call = False

            self.lexical_call_stack: pyc.TraceStack = self.make_stack()
            with self.lexical_call_stack.register_stack_state():
                self.cur_function: Optional[Any] = None
                self.num_args_seen = 0
                self.first_obj_id_in_chain: Optional[ObjId] = None
                self.top_level_node_id_for_chain: Optional[NodeId] = None
                self.saved_complex_symbol_load_data: Optional[
                    SavedComplexSymbolLoadData
                ] = None
                self.prev_node_id_in_cur_frame_lexical: Optional[NodeId] = None
                self.external_call_candidate: Optional[ExternalCallHandler] = None

                self.lexical_literal_stack: pyc.TraceStack = self.make_stack()
                with self.lexical_literal_stack.register_stack_state():
                    # `None` means use 'cur_frame_original_scope'
                    self.active_literal_scope: Optional[Namespace] = None

    def init_symtab(self) -> None:
        try:
            self.cur_cell_symtab = symtable.symtable(
                cells().current_cell().sanitized_content(),
                f"<cell-{cells().exec_counter()}>",
                "exec",
            )
        except Exception:
            pass

    @contextmanager
    def dataflow_tracing_disabled(self) -> Generator[None, None, None]:
        is_tracing_enabled = self.is_tracing_enabled
        try:
            if is_tracing_enabled:
                self._disable_tracing()
            yield
        finally:
            if is_tracing_enabled and not self.is_tracing_enabled:
                self._enable_tracing()

    @contextmanager
    def dataflow_tracing_disabled_patch(
        self, obj: Any, attr: str
    ) -> Generator[None, None, None]:
        orig_func = getattr(obj, attr)

        def new_func(*args, **kwargs):
            with self.dataflow_tracing_disabled():
                return orig_func(*args, **kwargs)

        try:
            setattr(obj, attr, new_func)
            yield
        finally:
            setattr(obj, attr, orig_func)

    @property
    def syntax_augmentation_specs(self) -> List[pyc.AugmentationSpec]:
        return [blocking_spec, cascading_reactive_spec, reactive_spec]

    @property
    def should_patch_meta_path(self) -> bool:
        return True

    def module_stmt_counter(self) -> int:
        return self._module_stmt_counter

    # TODO: use stack mechanism to automate this?
    def after_stmt_reset_hook(self) -> None:
        self.external_calls.clear()
        self.external_call_candidate = None
        self.active_scope = self.cur_frame_original_scope
        self.first_obj_id_in_chain = None
        self.top_level_node_id_for_chain = None
        self.saved_complex_symbol_load_data = None
        self.active_literal_scope = None
        self.node_id_to_loaded_literal_scope.clear()
        self.node_id_to_saved_dict_key.clear()
        self.prev_node_id_in_cur_frame = None
        self.saved_assign_rhs_obj = None
        flow().updated_symbols |= self.this_stmt_updated_symbols
        self.this_stmt_updated_symbols.clear()
        self._seen_functions_ids.clear()
        self.is_external_call_pending_return = False
        self.calling_symbol = None
        for sym, exclude_ns in self.pending_usage_updates_by_sym.items():
            sym.update_usage_info(exclude_ns=exclude_ns)
        self.pending_usage_updates_by_sym.clear()
        # don't clear the lexical stacks because line magics can
        # mess with when an 'after_stmt' gets emitted, and anyway
        # these should be pushed / popped appropriately by ast events

    def _handle_call_transition(self, trace_stmt: TraceStatement):
        if (
            self.external_call_depth
            >= flow().mut_settings.max_external_call_depth_for_tracing
        ):
            self._disable_tracing()
            return
        # ensures we only handle del's and not delitem's
        self.node_id_to_saved_del_data.clear()
        new_scope = trace_stmt.get_post_call_scope(trace_stmt.frame)
        with self.call_stack.push():
            # TODO: figure out a better way to determine if we're inside a lambda
            #  could this one lead to a false negative if a lambda is in the default of a function def kwarg?
            self.inside_anonymous_call = not isinstance(
                trace_stmt.stmt_node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            )
            self.cur_frame_original_scope = new_scope
            self.active_scope = new_scope
        self.prev_trace_stmt_in_cur_frame = self.prev_trace_stmt = trace_stmt

    def _check_prev_stmt_done_executing_hook(
        self, event: pyc.TraceEvent, trace_stmt: TraceStatement
    ):
        if event == pyc.after_stmt:  # and self.is_tracing_enabled:
            trace_stmt.finished_execution_hook()
        elif event == pyc.return_ and self.prev_event not in (
            pyc.call,
            pyc.exception,
        ):
            # ensuring prev != call ensures we're not inside of a stmt with multiple calls (such as map w/ lambda)
            if self.prev_trace_stmt is not None:
                self.prev_trace_stmt.finished_execution_hook()
            # prev_overall = self.prev_trace_stmt
            # if prev_overall is not None and prev_overall is not self._stack[-1][0]:
            #     # this condition ensures we're not inside of a stmt with multiple calls (such as map w/ lambda)
            #     prev_overall.finished_execution_hook()

    def _disable_tracing(self, *args, **kwargs) -> None:
        self.tracing_disabled_since_last_module_stmt = True
        super()._disable_tracing(*args, **kwargs)

    def _handle_return_transition(self, trace_stmt: TraceStatement, ret: Any):
        try:
            inside_anonymous_call = self.inside_anonymous_call
            try:
                return_to_stmt: TraceStatement = self.call_stack.get_field(
                    "prev_trace_stmt_in_cur_frame"
                )
            except IndexError:
                # then the first call was triggered from inside library code;
                # skip the transition and disable tracing in case this call
                # happens in a loop; we won't catch it in our normal tracing
                # disabler since it's the first call
                self._disable_tracing()
                return
            assert return_to_stmt is not None
            if self.prev_event != pyc.exception:
                # exception events are followed by return events until we hit an except clause
                # no need to track dependencies in this case
                if isinstance(return_to_stmt.stmt_node, ast.ClassDef):
                    return_to_stmt.class_scope = cast(
                        Namespace, self.cur_frame_original_scope
                    )
                elif (
                    isinstance(trace_stmt.stmt_node, ast.Return)
                    or inside_anonymous_call
                ):
                    if not trace_stmt.lambda_call_point_deps_done_once:
                        trace_stmt.lambda_call_point_deps_done_once = True
                        maybe_lambda_sym = flow().statement_to_func_cell.get(
                            id(trace_stmt.stmt_node), None
                        )
                        maybe_lambda_node = None
                        if maybe_lambda_sym is not None:
                            maybe_lambda_node = maybe_lambda_sym.func_def_stmt
                        if (
                            inside_anonymous_call
                            and maybe_lambda_node is not None
                            and isinstance(maybe_lambda_node, ast.Lambda)
                        ):
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
                                "<return_sym_%d>" % id(ret),
                                ret,
                                rvals,
                                trace_stmt.stmt_node,
                                is_anonymous=True,
                            )
                        if dsym_to_attach is not None:
                            return_to_node_id = self.call_stack.get_field(
                                "prev_node_id_in_cur_frame"
                            )
                            # logger.error("prev seen: %s", ast.dump(self.ast_node_by_id[return_to_node_id]))
                            try:
                                call_node_id = self.call_stack.get_field(
                                    "lexical_call_stack"
                                ).get_field("prev_node_id_in_cur_frame_lexical")
                                call_node = cast(
                                    ast.Call, self.ast_node_by_id[call_node_id]
                                )
                                # logger.error("prev seen outer: %s", ast.dump(self.ast_node_by_id[call_node_id]))
                                total_args = len(call_node.args) + len(
                                    call_node.keywords
                                )
                                num_args_seen = self.call_stack.get_field(
                                    "num_args_seen"
                                )
                                logger.warning("num args seen: %d", num_args_seen)
                                if total_args == num_args_seen:
                                    return_to_node_id = call_node_id
                                else:
                                    assert num_args_seen < total_args
                                    if num_args_seen < len(call_node.args):
                                        return_to_node_id = id(
                                            call_node.args[num_args_seen]
                                        )
                                    else:
                                        return_to_node_id = id(
                                            call_node.keywords[
                                                num_args_seen - len(call_node.args)
                                            ].value
                                        )
                            except IndexError:
                                pass
                            # logger.error("use node %s", ast.dump(self.ast_node_by_id[return_to_node_id]))
                            self.node_id_to_loaded_symbols.setdefault(
                                return_to_node_id, []
                            ).append(dsym_to_attach)
        finally:
            if self.is_tracing_enabled:
                self.call_stack.pop()
            if flow().is_dev_mode and len(self.call_stack) == 0:
                assert self.call_depth == 1

    def state_transition_hook(
        self,
        event: pyc.TraceEvent,
        trace_stmt: TraceStatement,
        ret: Any,
    ):
        self._check_prev_stmt_done_executing_hook(event, trace_stmt)

        if event == pyc.call:
            self._handle_call_transition(trace_stmt)
        if event == pyc.return_:
            self._handle_return_transition(trace_stmt, ret)
        self.prev_trace_stmt = trace_stmt
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

    def _resolve_store_data_for_simple_target(self, target: str, frame: FrameType):
        scope = self.cur_frame_original_scope
        lut = frame.f_locals
        if scope.symtab is not None:
            try:
                target_sym = scope.symtab.lookup(target)
                # this nonsense is necessary because the "is_nonlocal" method
                # is not available on Python <= 3.7;
                # the below check seems to work consistently across all Python versions
                is_nonlocal = getattr(
                    target_sym,
                    "is_nonlocal",
                    lambda: not target_sym.is_global()
                    and target_sym.is_assigned()
                    and target_sym.is_free(),
                )()
                if is_nonlocal:
                    scope = scope.parent_scope
                elif target_sym.is_global():
                    lut = frame.f_globals
                    scope = flow().global_scope
            except KeyError:
                pass
        try:
            obj = lut[target]
        except KeyError:
            obj = frame.f_globals[target]
            scope = flow().global_scope
        return scope, target, obj, False, set()

    def resolve_store_data_for_target(
        self, target: Union[str, int, ast.AST], frame: FrameType
    ) -> Tuple[Scope, AttrSubVal, Any, bool, Set[DataSymbol]]:
        target = self._partial_resolve_ref(target)
        if isinstance(target, str):
            return self._resolve_store_data_for_simple_target(target, frame)
        (scope, obj, attr_or_sub, is_subscript) = self.node_id_to_saved_store_data.pop(
            target
        )
        if isinstance(obj, (dict, list)):
            # we can be reasonably sure that the object on the rhs is the same thing
            # that gets stashed in `obj` for these cases, so use it instead of doing
            # the lookup (which may have side effects) to reduce intrusiveness
            attr_or_sub_obj = self.saved_assign_rhs_obj
        else:
            attr_or_sub_obj = flow().retrieve_namespace_attr_or_sub(
                obj, attr_or_sub, is_subscript
            )
        if attr_or_sub_obj is None:
            scope_to_use = scope
        else:
            scope_to_use = scope.get_earliest_ancestor_containing(
                id(attr_or_sub_obj), is_subscript
            )
        if scope_to_use is None:
            # Nobody before `scope` has it, so we'll insert it at this level
            scope_to_use = scope
        return (
            scope_to_use,
            attr_or_sub,
            attr_or_sub_obj,
            is_subscript,
            self.node_id_to_saved_live_subscript_refs.pop(target, set()),
        )

    def resolve_del_data_for_target(
        self, target: Union[str, int, ast.AST]
    ) -> Tuple[Scope, Optional[Any], AttrSubVal, bool]:
        target = self._partial_resolve_ref(target)
        if isinstance(target, str):
            return self.cur_frame_original_scope, None, target, False
        (scope, obj, attr_or_sub, is_subscript) = self.node_id_to_saved_del_data[target]
        return scope, obj, attr_or_sub, is_subscript

    def resolve_loaded_symbols(
        self, symbol_ref: Union[str, int, ast.AST, DataSymbol]
    ) -> List[DataSymbol]:
        if isinstance(symbol_ref, DataSymbol):
            return [symbol_ref]
        symbol_ref = self._partial_resolve_ref(symbol_ref)
        if isinstance(symbol_ref, int):
            return self.node_id_to_loaded_symbols.get(symbol_ref, [])
        elif isinstance(symbol_ref, str):
            ret = self.cur_frame_original_scope.lookup_data_symbol_by_name(symbol_ref)
            if ret is None:
                return []
            else:
                return [ret]
        else:
            return []

    def resolve_symbols(
        self, symbol_refs: Set[Union[str, int, DataSymbol]]
    ) -> Set[DataSymbol]:
        data_symbols = set()
        for ref in symbol_refs:
            data_symbols.update(self.resolve_loaded_symbols(ref))
        return data_symbols

    def _get_namespace_for_obj(
        self, obj: Any, obj_name: Optional[str] = None
    ) -> Namespace:
        obj_id = id(obj)
        ns = flow().namespaces.get(obj_id, None)
        if ns is not None:
            return ns
        class_scope = flow().namespaces.get(id(obj.__class__), None)
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
                scope_name = (
                    flow().get_first_full_symbol(obj_id).name
                    if obj_name is None
                    else obj_name
                )
            except AttributeError:
                scope_name = "<unknown namespace>"
            ns = Namespace(obj, scope_name, parent_scope=None)
        # FIXME: brittle strategy for determining parent scope of obj
        if ns.parent_scope is None:
            if (
                obj_name is not None
                and obj_name not in self.prev_trace_stmt_in_cur_frame.frame.f_locals
            ):
                parent_scope = flow().global_scope
            else:
                parent_scope = self.active_scope
            ns.parent_scope = parent_scope
        return ns

    def create_if_not_exists_module_symbol(
        self,
        module_or_function: Any,
        node: ast.AST,
        is_load: bool = True,
        is_named: bool = False,
    ) -> Optional[DataSymbol]:
        # TODO: upsert modules / namespaces hierarchically
        if isinstance(module_or_function, ModuleType):
            module = module_or_function
        else:
            module = getattr(module_or_function, "__module__", None)
            if module is None:
                # to handle numpy attributes
                module = getattr(
                    getattr(
                        getattr(module_or_function, "__self__", None), "__class__", None
                    ),
                    "__module__",
                    None,
                )
            module = sys.modules.get(cast(str, module))
        if module is None:
            return None
        module_name = module.__name__
        if module_name in (None, "builtins", "__main__"):
            return None
        is_first = True
        cur_scope = self.cur_frame_original_scope
        up_to_component = ""
        symbol = None
        components = module_name.split(".")
        for idx, component in enumerate(components):
            if is_first:
                up_to_component = component
            else:
                up_to_component = f"{up_to_component}.{component}"
            module = sys.modules.get(up_to_component)
            if module is None:
                return None
            sym_name = component
            if is_first and not is_named and not sym_name.startswith("<"):
                sym_name = f"<{sym_name}>"
            symbol = next(iter(flow().aliases.get(id(module), {None})))
            if symbol is None:
                symbol = cur_scope.upsert_data_symbol_for_name(
                    sym_name,
                    module,
                    set(),
                    self.prev_trace_stmt_in_cur_frame.stmt_node,
                    is_subscript=False,
                    is_module=True,
                    propagate=False,
                    implicit=not isinstance(node, (ast.Import, ast.ImportFrom)),
                    symbol_node=node,
                )
            is_first = False
            if idx == len(components) - 1:
                break
            symbol_namespace = symbol.namespace
            if symbol_namespace is None:
                cur_scope = Namespace(module, component, parent_scope=cur_scope)
            else:
                cur_scope = symbol_namespace
        if is_load and not symbol.is_implicit:
            self.node_id_to_loaded_symbols.setdefault(id(node), []).append(symbol)
        return symbol

    def _clear_info_and_maybe_lookup_or_create_complex_symbol(
        self, obj_attr_or_sub: Any, node: ast.AST
    ) -> Optional[DataSymbol]:
        if self.saved_complex_symbol_load_data is None:
            return None
        (
            scope,
            obj,
            attr_or_subscript,
            is_subscript,
            *_,
        ) = self.saved_complex_symbol_load_data
        self.saved_complex_symbol_load_data = None
        data_sym = scope.lookup_data_symbol_by_name_this_indentation(
            attr_or_subscript,
            is_subscript=is_subscript,
            skip_cloned_lookup=True,
        )
        logger.warning("found sym %s in scope %s", data_sym, scope)
        if data_sym is None:
            parent = scope.lookup_data_symbol_by_name_this_indentation(
                attr_or_subscript,
                is_subscript=is_subscript,
                skip_cloned_lookup=False,
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
                symbol_node=node,
            )
        elif data_sym.obj_id != id(obj_attr_or_sub):
            data_sym.update_obj_ref(obj_attr_or_sub)
        self.create_if_not_exists_module_symbol(obj_attr_or_sub, node, is_load=False)
        return data_sym

    @pyc.register_raw_handler(pyc.after_import)
    def after_import(self, *_, module: ModuleType, **__):
        compile_and_register_handlers_for_module(module)

    @pyc.register_raw_handler(
        (
            pyc.before_call,
            pyc.before_attribute_load,
            pyc.before_attribute_store,
            pyc.before_attribute_del,
            pyc.before_subscript_load,
            pyc.before_subscript_store,
            pyc.before_subscript_del,
        )
    )
    def _save_node_id(self, _obj, node_id: NodeId, frame, *_, **__):
        self.prev_node_id_in_cur_frame = node_id
        self.prev_node_id_in_cur_frame_lexical = node_id

    # @pyc.register_raw_handler((pyc.before_for_loop_body, pyc.before_while_loop_body))
    # def before_loop_body(self, _obj: Any, loop_id: NodeId, *_, **__):
    #     ret = self.is_tracing_enabled and loop_id not in self._seen_loop_ids
    #     if ret:
    #         self._seen_loop_ids.add(loop_id)
    #     return ret

    @pyc.register_raw_handler(
        (
            pyc.after_for_loop_iter,
            pyc.after_while_loop_iter,
            pyc.after_comprehension_if,
            pyc.after_comprehension_elt,
            pyc.after_dict_comprehension_key,
            pyc.after_dict_comprehension_value,
        )
    )
    def after_loop_iter(self, *_, guard: str, **__):
        self.activate_guard(guard)

    # @pyc.register_raw_handler(pyc.after_function_execution)
    # def after_function_exec(self, _obj: Any, _loop_id: NodeId, *_, guard: str, **__):
    #     self.activate_guard(guard)

    @pyc.register_raw_handler(pyc.after_assign_rhs)
    @pyc.skip_when_tracing_disabled
    def after_assign_rhs(self, obj: Any, *_, **__):
        self.saved_assign_rhs_obj = obj

    @pyc.register_raw_handler(pyc.after_subscript_slice)
    @pyc.skip_when_tracing_disabled
    def after_subscript_slice(self, _obj: Any, node_id: NodeId, *__, **___):
        node = self.ast_node_by_id.get(node_id, None)
        if node is None:
            return
        slice_node = cast(ast.Subscript, node).slice
        live, _ = compute_live_dead_symbol_refs(
            slice_node, scope=self.cur_frame_original_scope
        )
        subscript_live_refs = []
        for ref in live:
            if len(ref.ref.chain) == 1:
                subscript_live_refs.append(cast(str, ref.ref.chain[0].value))
        self.node_id_to_saved_live_subscript_refs[node_id] = self.resolve_symbols(
            set(subscript_live_refs)
        )
        Timestamp.update_usage_info(
            self.cur_frame_original_scope.lookup_data_symbol_by_name(ref)
            for ref in subscript_live_refs
        )

    @pyc.register_handler(
        (
            pyc.before_attribute_load,
            pyc.before_attribute_store,
            pyc.before_attribute_del,
            pyc.before_subscript_load,
            pyc.before_subscript_store,
            pyc.before_subscript_del,
        )
    )
    @pyc.skip_when_tracing_disabled
    def attrsub_tracer(
        self,
        obj: Any,
        node: Union[ast.Attribute, ast.Subscript],
        _frame_: FrameType,
        event: pyc.TraceEvent,
        *_,
        attr_or_subscript: AttrSubVal,
        call_context: bool,
        top_level_node_id: NodeId,
        obj_name: Optional[str] = None,
        **__,
    ):
        value_node_id = id(node.value)
        if isinstance(self.ast_node_by_id[value_node_id], ast.Call):
            # clear the callpoint dependency
            self.node_id_to_loaded_symbols.pop(value_node_id, None)
        if obj is None or obj is get_ipython():
            return
        logger.warning("%s %s of obj %s", event, attr_or_subscript, obj)
        sym_for_obj = self._clear_info_and_maybe_lookup_or_create_complex_symbol(
            obj, node
        )

        # Resolve symbol if necessary
        if sym_for_obj is None and obj_name is not None:
            sym_for_obj = self.active_scope.lookup_data_symbol_by_name_this_indentation(
                obj_name
            )

        scope = self._get_namespace_for_obj(obj, obj_name=obj_name)
        is_subscript = "subscript" in event.value
        if sym_for_obj is not None and sym_for_obj.obj is obj:
            try:
                data_sym = scope.lookup_data_symbol_by_name_this_indentation(
                    attr_or_subscript,
                    is_subscript=is_subscript,
                    skip_cloned_lookup=True,
                )
            except TypeError:
                data_sym = None
            self.pending_usage_updates_by_sym[
                sym_for_obj
            ] = self.pending_usage_updates_by_sym.get(sym_for_obj, True) and (
                data_sym is not None
            )

        obj_id = id(obj)
        if self.top_level_node_id_for_chain is None:
            self.top_level_node_id_for_chain = top_level_node_id
        if self.first_obj_id_in_chain is None:
            self.first_obj_id_in_chain = obj_id

        try:
            if isinstance(attr_or_subscript, list):
                attr_or_subscript = tuple(attr_or_subscript)
            if isinstance(attr_or_subscript, tuple):
                if not all(isinstance(v, (str, int)) for v in attr_or_subscript):
                    return
            elif not isinstance(attr_or_subscript, (str, int)):
                return
            if "store" in event.value:
                logger.warning(
                    "save store data for node id %d: %s, %s, %s, %s",
                    top_level_node_id,
                    scope,
                    obj,
                    attr_or_subscript,
                    is_subscript,
                )
                self.node_id_to_saved_store_data[top_level_node_id] = (
                    scope,
                    obj,
                    attr_or_subscript,
                    is_subscript,
                )
                return
            elif "del" in event.value:
                # logger.error("save del data for node %s", ast.dump(self.ast_node_by_id[top_level_node_id]))
                logger.warning("save del data for node id %d", top_level_node_id)
                self.node_id_to_saved_del_data[top_level_node_id] = (
                    scope,
                    obj,
                    attr_or_subscript,
                    is_subscript,
                )
                return
            logger.warning(
                "saved load data: %s, %s, %s", scope, attr_or_subscript, is_subscript
            )
            self.saved_complex_symbol_load_data = (
                scope,
                obj,
                attr_or_subscript,
                is_subscript,
                obj_name,
            )
            if call_context:
                if not is_subscript:
                    if (
                        sym_for_obj is None
                        and self.prev_trace_stmt_in_cur_frame is not None
                    ):
                        sym_for_obj = self.active_scope.upsert_data_symbol_for_name(
                            obj_name or "<anonymous_symbol_%d>" % id(obj),
                            obj,
                            set(),
                            self.prev_trace_stmt_in_cur_frame.stmt_node,
                            is_subscript=is_subscript,
                            is_anonymous=obj_name is None,
                            propagate=False,
                            implicit=True,
                            symbol_node=node,
                        )
                    if sym_for_obj is not None:
                        assert self.top_level_node_id_for_chain is not None
                        self.node_id_to_loaded_symbols.setdefault(
                            self.top_level_node_id_for_chain, []
                        ).append(sym_for_obj)
        finally:
            self.active_scope = scope

    @pyc.register_raw_handler(pyc.after_load_complex_symbol)
    def after_complex_symbol(self, obj: Any, node_id: NodeId, *_, **__):
        try:
            if not self.is_tracing_enabled:
                return
            if self.first_obj_id_in_chain is None:
                return
            assert self.top_level_node_id_for_chain is not None
            loaded_sym = self._clear_info_and_maybe_lookup_or_create_complex_symbol(
                obj, self.ast_node_by_id[node_id]
            )
            if loaded_sym is not None:
                self.node_id_to_loaded_symbols.setdefault(
                    self.top_level_node_id_for_chain, []
                ).append(loaded_sym)
        finally:
            self.saved_complex_symbol_load_data = None
            self.first_obj_id_in_chain = None
            self.top_level_node_id_for_chain = None
            self.active_scope = self.cur_frame_original_scope

    @pyc.register_handler(pyc.after_argument)
    @pyc.skip_when_tracing_disabled
    def handle_lift_argument(self, arg_obj: Any, arg_node: ast.AST, *_, **__):
        if self.cur_function not in (
            api_code,
            api_deps,
            api_lift,
            api_rdeps,
            api_rusers,
            api_timestamp,
            api_users,
            api_watchpoints,
        ):
            return
        resolved = [sym for sym in resolve_rval_symbols(arg_node) if sym.obj is arg_obj]
        if len(resolved) == 1:
            return next(iter(resolved))
        else:
            return pyc.Null

    def _resolve_external_call(self) -> None:
        try:
            external_call = self.lexical_call_stack.get_field("external_call_candidate")
        except IndexError:
            return
        if external_call is None:
            return
        self.external_calls.append(external_call)
        self.is_external_call_pending_return = True

    @pyc.register_raw_handler(pyc.after_argument)
    @pyc.skip_when_tracing_disabled
    def argument(
        self,
        arg_obj: Any,
        arg_node_id: int,
        *_,
        key: Optional[str],
        is_last: bool,
        **__,
    ):
        self.num_args_seen += 1
        try:
            ext_call_cand = self.lexical_call_stack.get_field("external_call_candidate")
        except IndexError:
            return
        if ext_call_cand is None:
            return
        arg_node = self.ast_node_by_id.get(arg_node_id, None)
        if isinstance(arg_node, ast.Name):
            assert self.active_scope is self.cur_frame_original_scope
            arg_dsym = self.active_scope.lookup_data_symbol_by_name(arg_node.id)
            if arg_dsym is None:
                self.active_scope.upsert_data_symbol_for_name(
                    arg_node.id,
                    arg_obj,
                    set(),
                    self.prev_trace_stmt_in_cur_frame.stmt_node,
                    implicit=True,
                    symbol_node=arg_node,
                )
        if key is None:
            ext_call_cand._process_arg_impl((arg_obj, resolve_rval_symbols(arg_node)))
        else:
            ext_call_cand._process_kwarg_impl(
                key, (arg_obj, resolve_rval_symbols(arg_node))
            )
        if is_last:
            self._resolve_external_call()

    def _save_external_call_candidate(
        self,
        module: Optional[ModuleType],
        obj: Optional[Any],
        function_or_method: Any,
        method_name: Optional[str],
        call_node: ast.Call,
    ) -> None:
        self.external_call_candidate = resolve_external_call(
            module, obj, function_or_method, method_name, call_node
        )

    @pyc.before_call
    @pyc.skip_when_tracing_disabled
    def before_call(self, function_or_method, node: ast.Call, *_, **__):
        if self.saved_complex_symbol_load_data is None:
            obj, attr_or_subscript, is_subscript, obj_name = None, None, None, None
            if isinstance(node.func, ast.Name):
                self.calling_symbol = (
                    self.cur_frame_original_scope.lookup_data_symbol_by_name(
                        node.func.id
                    )
                )
        else:
            # TODO: this will cause errors if we add more fields
            _ignored: Any
            (
                namespace,
                obj,
                attr_or_subscript,
                is_subscript,
                *_ignored,
                obj_name,
            ) = self.saved_complex_symbol_load_data
            self.calling_symbol = namespace.lookup_data_symbol_by_name(
                attr_or_subscript, is_subscript=is_subscript
            )
        # TODO: check if `function_or_method` has been registered as requiring a custom side effect
        if is_subscript:
            # TODO: need to do this also for chained calls, e.g. f()()
            method_name = None
        elif obj is None:
            method_name = None
        else:
            assert isinstance(attr_or_subscript, str)
            method_name = attr_or_subscript
            # method_name should match ast_by_id[function_or_method].func.id
        module_sym = self.create_if_not_exists_module_symbol(
            function_or_method, node.func
        )
        self._save_external_call_candidate(
            getattr(module_sym, "obj", None), obj, function_or_method, method_name, node
        )
        self.saved_complex_symbol_load_data = None
        with self.lexical_call_stack.push():
            self.cur_function = function_or_method
        self.active_scope = self.cur_frame_original_scope
        if len(node.args) + len(node.keywords) == 0:
            self._resolve_external_call()

    @pyc.register_raw_handler((pyc.before_function_body, pyc.before_lambda_body))
    def before_function_body(self, _obj: Any, function_id: NodeId, *_, **__):
        ret = self.is_tracing_enabled and function_id not in self._seen_functions_ids
        if ret:
            self._seen_functions_ids.add(function_id)
        return ret

    @pyc.register_raw_handler(pyc.after_call)
    def after_call(
        self,
        retval: Any,
        _node_id: NodeId,
        frame: FrameType,
        *_,
        **__,
    ):
        tracing_will_be_enabled_by_end = self.is_tracing_enabled
        if not self.is_tracing_enabled:
            tracing_will_be_enabled_by_end = self._should_attempt_to_reenable_tracing(
                frame
            )
            if tracing_will_be_enabled_by_end:
                # if tracing gets reenabled here instead of at the 'before_stmt' handler, then we're still
                # at the same module stmt as when tracing was disabled, and we still have a 'return' to trace
                self.call_depth = 1
                self.call_stack.clear()
                self.lexical_call_stack.clear()

        if not tracing_will_be_enabled_by_end:
            return

        # no need to reset active scope here;
        # that will happen in the 'after chain' handler

        if len(self.lexical_call_stack) > 0:
            # skip / give up if tracing was recently reenabled
            self.lexical_call_stack.pop()
        self.prev_node_id_in_cur_frame_lexical = None
        if self.is_external_call_pending_return:
            self.is_external_call_pending_return = False
            self.external_calls[-1].process_return(retval)

        if not self.is_tracing_enabled:
            self._enable_tracing()

    # Note: we don't trace set literals
    @pyc.register_raw_handler(
        (
            pyc.before_dict_literal,
            pyc.before_list_literal,
            pyc.before_tuple_literal,
        )
    )
    @pyc.skip_when_tracing_disabled
    def before_literal(self, *_, **__):
        parent_scope = self.active_literal_scope or self.cur_frame_original_scope
        with self.lexical_literal_stack.push():
            self.active_literal_scope = Namespace(
                None, Namespace.ANONYMOUS, parent_scope
            )

    @pyc.register_raw_handler(
        (
            pyc.after_dict_literal,
            pyc.after_list_literal,
            pyc.after_tuple_literal,
        )
    )
    @pyc.skip_when_tracing_disabled
    def after_literal(
        self, literal: Union[dict, list, tuple], node_id: NodeId, *_, **__
    ):
        try:
            self.active_literal_scope.update_obj_ref(literal)
            logger.warning("create literal scope %s", self.active_literal_scope)
            starred_idx = -1
            starred_namespace = None
            outer_deps = set()
            for (i, inner_obj), (
                inner_key_node,
                inner_val_node,
            ) in match_container_obj_or_namespace_with_literal_nodes(
                literal, self.ast_node_by_id[node_id]  # type: ignore
            ):
                # TODO: memoize symbol resolution; otherwise this will be quadratic for deeply nested literals
                if isinstance(inner_val_node, ast.Starred):
                    inner_symbols = set()
                    starred_idx += 1
                    if starred_idx == 0:
                        starred_syms = self.resolve_loaded_symbols(inner_val_node)
                        starred_namespace = (
                            flow().namespaces.get(starred_syms[0].obj_id, None)
                            if starred_syms
                            else None
                        )
                    if starred_namespace is not None:
                        starred_dep = starred_namespace.lookup_data_symbol_by_name_this_indentation(
                            starred_idx, is_subscript=True
                        )
                        inner_symbols.add(starred_dep)
                else:
                    inner_symbols = resolve_rval_symbols(inner_val_node)
                    if inner_key_node is not None:
                        outer_deps.update(resolve_rval_symbols(inner_key_node))
                self.node_id_to_loaded_symbols.pop(id(inner_val_node), None)
                inner_symbols.discard(None)
                if isinstance(
                    i, (int, str)
                ):  # TODO: perform more general check for SupportedIndexType
                    self.active_literal_scope.upsert_data_symbol_for_name(
                        i,
                        inner_obj,
                        inner_symbols,
                        self.prev_trace_stmt_in_cur_frame.stmt_node,
                        is_subscript=True,
                        implicit=True,
                        # this is necessary in case some literal object got reused,
                        # since as of this comment (2021/08/14) we do not clear
                        # GC'd symbols from the symbol graph
                        propagate=False,
                    )
            self.node_id_to_loaded_literal_scope[node_id] = self.active_literal_scope
            parent_scope: Scope = self.active_literal_scope.parent_scope
            while parent_scope.is_namespace_scope:
                parent_scope = parent_scope.parent_scope
            assert parent_scope is not None
            literal_sym = parent_scope.upsert_data_symbol_for_name(
                "<literal_sym_%d>" % id(literal),
                literal,
                outer_deps,
                self.prev_trace_stmt_in_cur_frame.stmt_node,
                is_anonymous=True,
                implicit=True,
                propagate=False,
            )
            self.node_id_to_loaded_symbols.setdefault(node_id, []).append(literal_sym)
            return literal
        finally:
            self.lexical_literal_stack.pop()

    @pyc.register_raw_handler(pyc.dict_key)
    @pyc.skip_when_tracing_disabled
    def dict_key(self, obj: Any, key_node_id: NodeId, *_, **__):
        self.node_id_to_saved_dict_key[key_node_id] = obj
        return obj

    @pyc.register_raw_handler(pyc.dict_value)
    @pyc.skip_when_tracing_disabled
    def dict_value(
        self,
        obj: Any,
        value_node_id: NodeId,
        *_,
        key_node_id: NodeId,
        dict_node_id: NodeId,
        **__,
    ):
        scope = self.node_id_to_loaded_literal_scope.pop(value_node_id, None)
        if scope is None:
            return obj
        # if we found a pending literal, assert that it's not dict unpacking
        assert key_node_id is not None
        key_obj = self.node_id_to_saved_dict_key.pop(key_node_id, None)
        if isinstance(key_obj, (str, int)):
            scope.scope_name = str(key_obj)
        return obj

    @pyc.register_raw_handler((pyc.list_elt, pyc.tuple_elt))
    @pyc.skip_when_tracing_disabled
    def list_or_tuple_elt(
        self,
        obj: Any,
        elt_node_id: NodeId,
        *_,
        index: Optional[int],
        container_node_id: NodeId,
        **__,
    ):
        scope = self.node_id_to_loaded_literal_scope.pop(elt_node_id, None)
        if scope is None:
            return obj
        if index is not None:
            scope.scope_name = str(index)
        return obj

    @pyc.register_raw_handler(pyc.after_lambda)
    @pyc.skip_when_tracing_disabled
    def after_lambda(self, obj: Any, lambda_node_id: int, frame: FrameType, *_, **__):
        sym_deps = []
        node = self.ast_node_by_id[lambda_node_id]
        for kw_default in node.args.defaults:  # type: ignore
            sym_deps.extend(self.resolve_loaded_symbols(kw_default))
        sym = self.active_scope.upsert_data_symbol_for_name(
            "<lambda_sym_%d>" % id(obj),
            obj,
            sym_deps,
            self.prev_trace_stmt_in_cur_frame.stmt_node,
            is_function_def=True,
            propagate=False,
            symbol_node=node,
        )
        sym.func_def_stmt = node
        self.node_id_to_loaded_symbols.setdefault(lambda_node_id, []).append(sym)

    @pyc.register_raw_handler(pyc.after_stmt)
    def after_stmt(self, ret_expr: Any, stmt_id: int, frame: FrameType, *_, **__):
        if stmt_id in self.seen_stmts:
            return ret_expr
        self._saved_stmt_ret_expr = ret_expr
        stmt = self.ast_node_by_id.get(stmt_id, None)
        if stmt is not None:
            self.handle_other_sys_events(
                None, 0, frame, pyc.after_stmt, stmt_node=cast(ast.stmt, stmt)
            )
        active_watchpoints = flow().active_watchpoints
        if active_watchpoints:
            if sys.version_info < (3, 7):
                logger.warning("skipping watchpoint on Python < 3.7")
            else:
                breakpoint()
            active_watchpoints.clear()
        return ret_expr

    @pyc.register_raw_handler(pyc.after_module_stmt)
    def after_module_stmt(self, _ret, node_id, *_, **__) -> Optional[Any]:
        if self.is_tracing_enabled:
            assert self.cur_frame_original_scope.is_global
        ret = self._saved_stmt_ret_expr
        self._saved_stmt_ret_expr = None
        if ret is not None:
            stmt: ast.stmt = self.ast_node_by_id[node_id]
            flow().global_scope.upsert_data_symbol_for_name(
                "_",
                ret,
                resolve_rval_symbols(stmt, should_update_usage_info=False),
                stmt,
            )
        self._module_stmt_counter += 1
        self.tracing_disabled_since_last_module_stmt = False
        return ret

    @pyc.register_raw_handler(pyc.before_stmt)
    def before_stmt(self, _ret: None, stmt_id: int, frame: FrameType, *_, **__) -> None:
        self.next_stmt_node_id = stmt_id
        if stmt_id in self.seen_stmts:
            return
        # logger.warning('reenable tracing: %s', site_id)
        if self.prev_trace_stmt_in_cur_frame is not None:
            prev_trace_stmt_in_cur_frame = self.prev_trace_stmt_in_cur_frame
            # both of the following stmts should be processed when body is entered
            if isinstance(
                prev_trace_stmt_in_cur_frame.stmt_node, (ast.For, ast.If, ast.With)
            ):
                self.after_stmt(None, prev_trace_stmt_in_cur_frame.stmt_id, frame)
        trace_stmt = self.traced_statements.get(stmt_id, None)
        if trace_stmt is None:
            trace_stmt = TraceStatement(
                frame, cast(ast.stmt, self.ast_node_by_id[stmt_id])
            )
            self.traced_statements[stmt_id] = trace_stmt
        self.prev_trace_stmt_in_cur_frame = trace_stmt
        if not self.is_tracing_enabled and self._should_attempt_to_reenable_tracing(
            frame
        ):
            # At this point, we can be sure we're at the top level
            # because tracing was enabled in a top-level handler.
            # We also need to clear the stack, as we won't catch
            # the return event (since tracing was already disabled
            # when we got to a `before_stmt` event).
            self.call_depth = 0
            self.call_stack.clear()
            self.lexical_call_stack.clear()
            self.after_stmt_reset_hook()
            self._enable_tracing()

    def _should_attempt_to_reenable_tracing(self, frame: FrameType) -> bool:
        if flow().is_dev_mode:
            assert not self.is_tracing_enabled
            assert self.call_depth > 0, (
                "expected managed call depth > 0, got %d" % self.call_depth
            )
        call_depth = 0
        while frame is not None:
            if flow().is_cell_file(frame.f_code.co_filename):
                call_depth += 1
            frame = frame.f_back
        if flow().is_dev_mode:
            assert call_depth >= 1, "expected call depth >= 1, got %d" % call_depth
        # TODO: allow reenabling tracing beyond just at the top level
        if call_depth != 1:
            return False
        if len(self.call_stack) == 0:
            stmt_in_top_level_frame = self.prev_trace_stmt_in_cur_frame
        else:
            stmt_in_top_level_frame = self.call_stack.get_field(
                "prev_trace_stmt_in_cur_frame", depth=0
            )
        if stmt_in_top_level_frame.finished:
            return False
        if flow().trace_messages_enabled:
            self.EVENT_LOGGER.warning("reenable tracing >>>")
        return True

    def _get_or_make_trace_stmt(
        self, stmt_node: ast.stmt, frame: FrameType
    ) -> TraceStatement:
        trace_stmt = self.traced_statements.get(id(stmt_node), None)
        if trace_stmt is None:
            trace_stmt = TraceStatement(frame, stmt_node)
            self.traced_statements[id(stmt_node)] = trace_stmt
        return trace_stmt

    def _maybe_log_event(
        self, event: pyc.TraceEvent, stmt_node: ast.stmt, trace_stmt: TraceStatement
    ):
        if flow().trace_messages_enabled:
            codeline = astunparse.unparse(stmt_node).strip("\n").split("\n")[0]
            codeline = " " * getattr(stmt_node, "col_offset", 0) + codeline
            self.EVENT_LOGGER.warning(
                " %3d: %10s >>> %s", trace_stmt.lineno, event, codeline
            )

    def _get_stmt_node_for_sys_event(
        self, event: pyc.TraceEvent, cell_num: int, lineno: int
    ) -> Optional[ast.stmt]:
        if event == pyc.return_ and self.next_stmt_node_id is not None:
            # this branch necessary for python < 3.8 where the frame
            # position maps to the calling location instead of the return
            return cast(ast.stmt, self.ast_node_by_id[self.next_stmt_node_id])
        try:
            stmt_node = self.stmt_by_lineno_by_module_id[cell_num][lineno]
            if event == pyc.call and not isinstance(
                stmt_node, (ast.AsyncFunctionDef, ast.FunctionDef)
            ):
                # TODO: this is bad and I should feel bad. Need a better way to figure out which
                #  stmt is executing than by using line numbers.
                parent_node = self.parent_stmt_by_id.get(id(stmt_node), None)
                if flow().is_dev_mode:
                    logger.info(
                        "node %s parent %s",
                        ast.dump(stmt_node),
                        None if parent_node is None else ast.dump(parent_node),
                    )
                if (
                    parent_node is not None
                    and getattr(parent_node, "lineno", None) == lineno
                    and isinstance(parent_node, (ast.AsyncFunctionDef, ast.FunctionDef))
                ):
                    stmt_node = parent_node
            return stmt_node
        except KeyError as e:
            if flow().is_dev_mode:
                self.EVENT_LOGGER.warning(
                    "got key error for stmt node in cell %d, line %d",
                    cell_num,
                    lineno,
                )
                raise e
        return None

    @pyc.register_raw_handler(pyc.call)
    def handle_call(
        self,
        ret_obj: Any,
        _node_id: None,
        frame: FrameType,
        event: pyc.TraceEvent,
        *_,
        **__,
    ):
        cell_num, lineno = flow().get_position(frame)
        assert cell_num is not None
        stmt_node = self._get_stmt_node_for_sys_event(event, cell_num, lineno)
        trace_stmt = self._get_or_make_trace_stmt(stmt_node, frame)
        self._maybe_log_event(event, stmt_node, trace_stmt)

        try:
            prev_node_id_in_cur_frame_lexical = self.lexical_call_stack.get_field(
                "prev_node_id_in_cur_frame_lexical"
            )
        except IndexError:
            # this could happen if the call happens in library code,
            # and the corresponding notebook statement isn't an ast.Call
            # (e.g., it's a property or just induces a __repr__ call)
            # Make node_id_for_last_call point to self to cover such cases
            prev_node_id_in_cur_frame_lexical = id(stmt_node)

        if trace_stmt.node_id_for_last_call == prev_node_id_in_cur_frame_lexical:
            if flow().trace_messages_enabled:
                self.EVENT_LOGGER.warning(" disable tracing >>>")
            self._disable_tracing()
            return pyc.Null
        trace_stmt.node_id_for_last_call = prev_node_id_in_cur_frame_lexical
        self.state_transition_hook(event, trace_stmt, ret_obj)

    @pyc.register_raw_handler((pyc.return_, pyc.exception))
    def handle_other_sys_events(
        self,
        ret_obj: Any,
        _node_id: None,
        frame: FrameType,
        event: pyc.TraceEvent,
        *_,
        stmt_node: Optional[ast.stmt] = None,
        **__,
    ):
        assert self.is_tracing_enabled or event == pyc.after_stmt

        cell_num, lineno = flow().get_position(frame)
        assert cell_num is not None

        if event == pyc.after_stmt:
            assert stmt_node is not None
        else:
            stmt_node = self._get_stmt_node_for_sys_event(event, cell_num, lineno)

        trace_stmt = self._get_or_make_trace_stmt(stmt_node, frame)
        self._maybe_log_event(event, stmt_node, trace_stmt)
        self.state_transition_hook(event, trace_stmt, ret_obj)
