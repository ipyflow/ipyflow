# -*- coding: future_annotations -*-
import ast
import logging
import sys
from contextlib import contextmanager
from typing import cast, TYPE_CHECKING

from nbsafety.extra_builtins import TRACING_ENABLED, make_loop_guard_name
from nbsafety.tracing.trace_events import TraceEvent
from nbsafety.utils.ast_utils import EmitterMixin, make_test, make_composite_condition, subscript_to_slice
from nbsafety.utils import fast

if TYPE_CHECKING:
    from typing import Dict, FrozenSet, List, Optional, Set, Union


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class ExprRewriter(ast.NodeTransformer, EmitterMixin):
    def __init__(self, orig_to_copy_mapping: Dict[int, ast.AST], events_with_handlers: FrozenSet[TraceEvent]):
        EmitterMixin.__init__(self, orig_to_copy_mapping, events_with_handlers)
        self._top_level_node_for_symbol: Optional[ast.AST] = None

    def visit(self, node: ast.AST):
        ret = super().visit(node)
        if isinstance(node, ast.stmt):
            # we haven't inserted statements yet, and StatementInserter needs the previous ids to be identical
            assert ret is node
        return ret

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Load) and TraceEvent.load_name in self._events_with_handlers:
            with fast.location_of(node):
                return fast.Call(
                    func=self.emitter_ast(),
                    args=[
                        TraceEvent.load_name.to_ast(),
                        self.get_copy_id_ast(node),
                    ],
                    keywords=fast.kwargs(ret=node),
                )
        else:
            return node

    @contextmanager
    def attrsub_context(self, top_level_node: Optional[ast.AST]):
        old = self._top_level_node_for_symbol
        if old is None or top_level_node is None:
            # entering context when we are already inside chain is a no-op,
            # but we can specify a context of not being in chain if we are
            # inside one (in order to support arguments)
            self._top_level_node_for_symbol = top_level_node
        yield
        self._top_level_node_for_symbol = old

    @property
    def _inside_attrsub_load_chain(self):
        return self._top_level_node_for_symbol is not None

    def visit_Attribute(self, node: ast.Attribute, call_context=False):
        with fast.location_of(node.value):
            attr_node = cast(ast.Attribute, node)
            attr_or_sub = fast.Str(attr_node.attr)
        return self.visit_Attribute_or_Subscript(node, attr_or_sub, call_context=call_context)

    def _maybe_convert_ast_subscript(self, subscript: ast.AST) -> ast.expr:
        if isinstance(subscript, ast.Index):
            return self.visit(subscript.value)  # type: ignore
        elif isinstance(subscript, ast.Slice):
            lower = fast.NameConstant(None) if subscript.lower is None else self.visit(subscript.lower)
            upper = fast.NameConstant(None) if subscript.upper is None else self.visit(subscript.upper)
            return fast.Call(
                func=fast.Name('slice', ast.Load()),
                args=[lower, upper] + ([] if subscript.step is None else [self.visit(subscript.step)]),
                keywords=[],
            )
        else:
            return cast(ast.expr, subscript)

    def visit_Subscript(self, node: ast.Subscript, call_context=False):
        with self.attrsub_context(None):
            with fast.location_of(node.slice if hasattr(node.slice, 'lineno') else node.value):
                slc = self._maybe_convert_ast_subscript(node.slice)
                if isinstance(slc, (ast.ExtSlice, ast.Tuple)):
                    elts = slc.elts if isinstance(slc, ast.Tuple) else slc.dims  # type: ignore
                    elts = [self._maybe_convert_ast_subscript(elt) for elt in elts]  # type: ignore
                    slc = fast.Tuple(elts, ast.Load())
                if TraceEvent.subscript_slice in self._events_with_handlers:
                    slc = fast.Call(
                        func=self.emitter_ast(),
                        args=[
                            TraceEvent.subscript_slice.to_ast(),
                            self.get_copy_id_ast(node),
                        ],
                        keywords=fast.kwargs(ret=cast(ast.expr, slc)),
                    )
                if TraceEvent.subscript in self._events_with_handlers:
                    replacement_slice: ast.expr = fast.Call(
                        func=self.emitter_ast(),
                        args=[
                            TraceEvent._load_saved_slice.to_ast(),
                            self.get_copy_id_ast(node.slice),
                        ],
                        keywords=[],
                    )
                else:
                    replacement_slice = slc
                if sys.version_info >= (3, 9):
                    node.slice = replacement_slice
                else:
                    node.slice = fast.Index(replacement_slice)
        return self.visit_Attribute_or_Subscript(node, slc, call_context=call_context)

    def _maybe_wrap_symbol_in_before_after_tracing(
        self, node, call_context=False, orig_node_id=None, begin_kwargs=None, end_kwargs=None
    ):
        if self._inside_attrsub_load_chain:
            return node
        orig_node = node
        orig_node_id = orig_node_id or id(orig_node)
        end_kwargs = end_kwargs or {}

        ctx = getattr(orig_node, 'ctx', ast.Load())
        is_load = isinstance(ctx, ast.Load)

        with fast.location_of(node):
            if is_load:
                node = orig_node
            elif isinstance(orig_node, (ast.Attribute, ast.Subscript)):
                node = orig_node.value
            else:
                raise TypeError('Unsupported node type for before / after symbol tracing: %s', type(orig_node))
            end_kwargs['ctx'] = fast.Str(ctx.__class__.__name__)
            end_kwargs['call_context'] = fast.NameConstant(call_context)
            if TraceEvent.before_complex_symbol in self._events_with_handlers:
                node = self.make_tuple_event_for(node, TraceEvent.before_complex_symbol, orig_node_id=orig_node_id)
            end_kwargs['ret'] = node
            if TraceEvent.after_complex_symbol in self._events_with_handlers:
                node = fast.Call(
                    func=self.emitter_ast(),
                    args=[TraceEvent.after_complex_symbol.to_ast(), self.get_copy_id_ast(orig_node_id)],
                    keywords=fast.kwargs(**end_kwargs),
                )
            if not is_load:
                if isinstance(orig_node, ast.Attribute):
                    node = fast.Attribute(
                        value=node,
                        attr=orig_node.attr,
                    )
                elif isinstance(orig_node, ast.Subscript):
                    node = fast.Subscript(
                        value=node,
                        slice=orig_node.slice,
                    )
                else:
                    raise TypeError(
                        'Symbol tracing stores unsupported for node %s with type %s' %(orig_node, type(orig_node))
                    )
                node.ctx = ast.Store()
        # end location_of(node)
        return node

    def visit_Attribute_or_Subscript(
        self,
        node: Union[ast.Attribute, ast.Subscript],
        attr_or_sub: ast.expr,
        call_context: bool = False
    ):
        orig_node_id = id(node)
        with fast.location_of(node.value):
            extra_args: List[ast.keyword] = []
            if isinstance(node.value, ast.Name):
                extra_args = fast.kwargs(obj_name=fast.Str(node.value.id))

            subscript_name = None
            if isinstance(node, ast.Subscript):
                slice_val = subscript_to_slice(node)
                if isinstance(slice_val, ast.Name):
                    # TODO: this should be more general than just simple ast.Name subscripts
                    subscript_name = slice_val.id

            evt_to_use = TraceEvent.subscript if isinstance(node, ast.Subscript) else TraceEvent.attribute
            should_emit_evt = evt_to_use in self._events_with_handlers
            should_emit_evt = should_emit_evt or (
                evt_to_use == TraceEvent.subscript and TraceEvent._load_saved_slice in self._events_with_handlers
            )
            orig_node_value = node.value
            with self.attrsub_context(node):
                node.value = self.visit(node.value)
                if should_emit_evt:
                    node.value = fast.Call(
                        func=self.emitter_ast(),
                        args=[evt_to_use.to_ast(), self.get_copy_id_ast(orig_node_value)],
                        keywords=fast.kwargs(
                            ret=node.value,
                            attr_or_subscript=attr_or_sub,
                            ctx=fast.Str(node.ctx.__class__.__name__),
                            call_context=fast.NameConstant(call_context),
                            top_level_node_id=self.get_copy_id_ast(self._top_level_node_for_symbol),
                            subscript_name=(
                                fast.NameConstant(None) if subscript_name is None else fast.Str(subscript_name)
                            ),
                        ) + extra_args
                    )
        # end fast.location_of(node.value)

        return self._maybe_wrap_symbol_in_before_after_tracing(node, orig_node_id=orig_node_id)

    def _get_replacement_args(self, args, keywords: bool):
        replacement_args = []
        for arg in args:
            is_starred = isinstance(arg, ast.Starred)
            is_kwstarred = keywords and arg.arg is None
            if keywords or is_starred:
                maybe_kwarg = getattr(arg, 'value')
            else:
                maybe_kwarg = arg
            with fast.location_of(maybe_kwarg):
                with self.attrsub_context(None):
                    new_arg_value = self.visit(maybe_kwarg)
                if TraceEvent.argument in self._events_with_handlers:
                    with self.attrsub_context(None):
                        new_arg_value = cast(ast.expr, fast.Call(
                            func=self.emitter_ast(),
                            args=[TraceEvent.argument.to_ast(), self.get_copy_id_ast(maybe_kwarg)],
                            keywords=fast.kwargs(
                                ret=new_arg_value,
                                is_starred=fast.NameConstant(is_starred),
                                is_kwstarred=fast.NameConstant(is_kwstarred)
                            ),
                        ))
                if keywords or is_starred:
                    setattr(arg, 'value', new_arg_value)
                else:
                    arg = new_arg_value
            replacement_args.append(arg)
        return replacement_args

    def visit_Call(self, node: ast.Call):
        orig_node_id = id(node)

        with self.attrsub_context(node):
            if isinstance(node.func, ast.Attribute):
                node.func = self.visit_Attribute(node.func, call_context=True)
            elif isinstance(node.func, ast.Subscript):
                node.func = self.visit_Subscript(node.func, call_context=True)
            else:
                node.func = self.visit(node.func)

        # TODO: need a way to rewrite ast of subscript args,
        #  and to process these separately from outer rewrite

        node.args = self._get_replacement_args(node.args, False)
        node.keywords = self._get_replacement_args(node.keywords, True)

        # in order to ensure that the args are processed with appropriate active scope,
        # we need to make sure not to use the active namespace scope on args (in the case
        # of a function call on an ast.Attribute).
        #
        # We do so by emitting an "enter argument list", whose handler pushes the current active
        # scope while we process each argument. The "end argument list" event will then restore
        # the active scope.
        #
        # This effectively rewrites function calls as follows:
        # f(a, b, ..., c) -> trace(f, 'enter argument list')(a, b, ..., c)
        if TraceEvent.before_call in self._events_with_handlers:
            with fast.location_of(node.func):
                node.func = fast.Call(
                    func=self.emitter_ast(),
                    args=[TraceEvent.before_call.to_ast(), self.get_copy_id_ast(orig_node_id)],
                    keywords=fast.kwargs(
                        ret=node.func,
                        call_node_id=self.get_copy_id_ast(orig_node_id),
                    ),
                )

        # f(a, b, ..., c) -> trace(f(a, b, ..., c), 'exit argument list')
        if TraceEvent.after_call in self._events_with_handlers:
            with fast.location_of(node):
                node = fast.Call(
                    func=self.emitter_ast(),
                    args=[TraceEvent.after_call.to_ast(), self.get_copy_id_ast(orig_node_id)],
                    keywords=fast.kwargs(
                        ret=node,
                        call_node_id=self.get_copy_id_ast(orig_node_id),
                    ),
                )

        return self._maybe_wrap_symbol_in_before_after_tracing(node, call_context=True, orig_node_id=orig_node_id)

    def visit_Assign(self, node: ast.Assign):
        new_targets = []
        for target in node.targets:
            new_targets.append(self.visit(target))
        node.targets = new_targets
        orig_value_id = id(node.value)
        with fast.location_of(node.value):
            node.value = self.visit(node.value)
            if TraceEvent.before_assign_rhs in self._events_with_handlers:
                node.value = self.make_tuple_event_for(
                    node.value, TraceEvent.before_assign_rhs, orig_node_id=orig_value_id
                )
            if TraceEvent.after_assign_rhs in self._events_with_handlers:
                node.value = fast.Call(
                    func=self.emitter_ast(),
                    args=[TraceEvent.after_assign_rhs.to_ast(), self.get_copy_id_ast(orig_value_id)],
                    keywords=fast.kwargs(ret=node.value),
                )
        return node

    def visit_Lambda(self, node: ast.Lambda):
        assert isinstance(getattr(node, 'ctx', ast.Load()), ast.Load)
        untraced_lam = cast(ast.Lambda, self._orig_to_copy_mapping[id(node)])
        ret_node: ast.Lambda = cast(ast.Lambda, self.generic_visit(node))
        with fast.location_of(node):
            ret_node.body = fast.IfExp(
                test=make_composite_condition([
                    make_test(TRACING_ENABLED),
                    fast.Call(
                        func=self.emitter_ast(),
                        args=[TraceEvent.before_lambda_body.to_ast(), self.get_copy_id_ast(node)],
                        keywords=fast.kwargs(ret=fast.NameConstant(True)),
                    ) if TraceEvent.before_lambda_body in self._events_with_handlers else None,
                ]),
                body=ret_node.body,
                orelse=untraced_lam.body,
            )
            if TraceEvent.before_lambda in self._events_with_handlers:
                ret_node = self.make_tuple_event_for(
                    ret_node, TraceEvent.before_lambda, orig_node_id=id(node)
                )
            if TraceEvent.after_lambda in self._events_with_handlers:
                ret_node = fast.Call(  # type: ignore
                    func=self.emitter_ast(),
                    args=[TraceEvent.after_lambda.to_ast(), self.get_copy_id_ast(node)],
                    keywords=fast.kwargs(ret=ret_node),
                )
        return ret_node

    def visit_While(self, node: ast.While):
        for name, field in ast.iter_fields(node):
            if name == 'test':
                loop_node_copy = cast(ast.While, self._orig_to_copy_mapping[id(node)])
                loop_guard = make_loop_guard_name(loop_node_copy)
                with fast.location_of(node):
                    node.test = fast.IfExp(
                        test=make_composite_condition([
                            make_test(TRACING_ENABLED),
                            make_test(loop_guard, negate=True),
                        ]),
                        body=self.visit(field),
                        orelse=loop_node_copy.test,
                    )
            elif isinstance(field, list):
                setattr(node, name, [self.visit(elt) for elt in field])
            else:
                setattr(node, name, self.visit(field))
        return node

    @staticmethod
    def _ast_container_to_literal_trace_evt(
        node: Union[ast.Dict, ast.List, ast.Set, ast.Tuple], before: bool
    ) -> TraceEvent:
        if isinstance(node, ast.Dict):
            return TraceEvent.before_dict_literal if before else TraceEvent.after_dict_literal
        elif isinstance(node, ast.List):
            return TraceEvent.before_list_literal if before else TraceEvent.after_list_literal
        elif isinstance(node, ast.Set):
            return TraceEvent.before_set_literal if before else TraceEvent.after_set_literal
        elif isinstance(node, ast.Tuple):
            return TraceEvent.before_tuple_literal if before else TraceEvent.after_tuple_literal
        else:
            raise TypeError('invalid ast node: %s', ast.dump(node))

    def visit_literal(self, node: Union[ast.Dict, ast.List, ast.Set, ast.Tuple], should_inner_visit=True):
        ret_node: ast.expr = node
        if should_inner_visit:
            ret_node = cast(ast.expr, self.generic_visit(node))
        if not isinstance(getattr(node, 'ctx', ast.Load()), ast.Load):
            return ret_node
        with fast.location_of(node):
            lit_before_evt = self._ast_container_to_literal_trace_evt(node, before=True)
            if lit_before_evt in self._events_with_handlers:
                ret_node = self.make_tuple_event_for(ret_node, lit_before_evt, orig_node_id=id(node))
            lit_after_evt = self._ast_container_to_literal_trace_evt(node, before=False)
            if lit_after_evt in self._events_with_handlers:
                ret_node = fast.Call(
                    func=self.emitter_ast(),
                    args=[lit_after_evt.to_ast(), self.get_copy_id_ast(node)],
                    keywords=fast.kwargs(ret=ret_node),
                )
        return ret_node

    def visit_List(self, node: ast.List):
        return self.visit_List_or_Set_or_Tuple(node)

    def visit_Set(self, node: ast.Set):
        return self.visit_List_or_Set_or_Tuple(node)

    def visit_Tuple(self, node: ast.Tuple):
        return self.visit_List_or_Set_or_Tuple(node)

    @staticmethod
    def _ast_container_to_elt_trace_evt(node: Union[ast.List, ast.Set, ast.Tuple]) -> TraceEvent:
        if isinstance(node, ast.List):
            return TraceEvent.list_elt
        elif isinstance(node, ast.Set):
            return TraceEvent.set_elt
        elif isinstance(node, ast.Tuple):
            return TraceEvent.tuple_elt
        else:
            raise TypeError('invalid ast node: %s', ast.dump(node))

    def visit_List_or_Set_or_Tuple(self, node: Union[ast.List, ast.Set, ast.Tuple]):
        traced_elts: List[ast.expr] = []
        is_load = isinstance(getattr(node, 'ctx', ast.Load()), ast.Load)
        saw_starred = False
        elt_trace_evt = self._ast_container_to_elt_trace_evt(node)
        for i, elt in enumerate(node.elts):
            if isinstance(elt, ast.Starred):
                # TODO: trace starred elts too
                saw_starred = True
                traced_elts.append(elt)
                continue
            elif not is_load or elt_trace_evt not in self._events_with_handlers:
                traced_elts.append(self.visit(elt))
                continue
            with fast.location_of(elt):
                traced_elts.append(fast.Call(
                    func=self.emitter_ast(),
                    args=[elt_trace_evt.to_ast(), self.get_copy_id_ast(elt)],
                    keywords=fast.kwargs(
                        ret=self.visit(elt),
                        index=fast.NameConstant(None) if saw_starred else fast.Num(i),
                        container_node_id=self.get_copy_id_ast(node),
                    )
                ))
        node.elts = traced_elts
        return self.visit_literal(node, should_inner_visit=False)

    def visit_Dict(self, node: ast.Dict):
        traced_keys: List[Optional[ast.expr]] = []
        traced_values: List[ast.expr] = []
        for k, v in zip(node.keys, node.values):
            is_dict_unpack = (k is None)
            if is_dict_unpack:
                traced_keys.append(None)
            else:
                with fast.location_of(k):
                    traced_key = self.visit(k)
                    if TraceEvent.dict_key in self._events_with_handlers:
                        traced_key = fast.Call(
                            func=self.emitter_ast(),
                            args=[TraceEvent.dict_key.to_ast(), self.get_copy_id_ast(k)],
                            keywords=fast.kwargs(
                                ret=traced_key,
                                value_node_id=self.get_copy_id_ast(v),
                                dict_node_id=self.get_copy_id_ast(node),
                            )
                        )
                    traced_keys.append(traced_key)
            with fast.location_of(v):
                if is_dict_unpack:
                    key_node_id_ast = fast.NameConstant(None)
                else:
                    key_node_id_ast = self.get_copy_id_ast(k)
                traced_value = self.visit(v)
                if TraceEvent.dict_value in self._events_with_handlers:
                    traced_value = fast.Call(
                        func=self.emitter_ast(),
                        args=[TraceEvent.dict_value.to_ast(), self.get_copy_id_ast(v)],
                        keywords=fast.kwargs(
                            ret=traced_value,
                            key_node_id=key_node_id_ast,
                            dict_node_id=self.get_copy_id_ast(node),
                        )
                    )
                traced_values.append(traced_value)
        node.keys = traced_keys
        node.values = traced_values
        return self.visit_literal(node, should_inner_visit=False)

    def visit_Return(self, node: ast.Return):
        if node.value is None:
            return node
        orig_node_value = node.value
        with fast.location_of(node):
            node.value = self.visit(node.value)
            if TraceEvent.before_return in self._events_with_handlers:
                node.value = self.make_tuple_event_for(
                    node.value, TraceEvent.before_return, orig_node_id=id(orig_node_value)
                )
            if TraceEvent.after_return in self._events_with_handlers:
                node.value = fast.Call(
                    func=self.emitter_ast(),
                    args=[TraceEvent.after_return.to_ast(), self.get_copy_id_ast(orig_node_value)],
                    keywords=fast.kwargs(ret=node.value),
                )
        return node

    def visit_Delete(self, node: ast.Delete):
        ret = cast(ast.Delete, self.generic_visit(node))
        for target in ret.targets:
            target.ctx = ast.Del()  # type: ignore
        return ret
