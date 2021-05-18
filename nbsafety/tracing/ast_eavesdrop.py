# -*- coding: future_annotations -*-
import ast
from contextlib import contextmanager
import logging
from typing import cast, TYPE_CHECKING
import sys

from nbsafety.analysis.attr_symbols import resolve_slice_to_constant
from nbsafety.tracing.trace_events import TraceEvent, EMIT_EVENT
from nbsafety.utils import fast

if TYPE_CHECKING:
    from typing import Dict, List, Optional, Set, Union


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


def _maybe_convert_ast_subscript(subscript: ast.AST) -> ast.AST:
    if isinstance(subscript, ast.Index):
        return subscript.value  # type: ignore
    elif isinstance(subscript, ast.Slice):
        lower = fast.NameConstant(None) if subscript.lower is None else subscript.lower
        upper = fast.NameConstant(None) if subscript.upper is None else subscript.upper
        return fast.Call(
            func=fast.Name('slice', ast.Load()),
            args=[lower, upper] + ([] if subscript.step is None else [subscript.step]),
            keywords=[],
        )
    else:
        return subscript


class AstEavesdropper(ast.NodeTransformer):
    def __init__(self, orig_to_copy_mapping: Dict[int, ast.AST]):
        self._orig_to_copy_mapping = orig_to_copy_mapping
        self._top_level_node_for_symbol: Optional[ast.AST] = None

    def _emitter_ast(self):
        return fast.Name(EMIT_EVENT, ast.Load())

    def _get_copy_id_ast(self, orig_node_id: Union[int, ast.AST]):
        if not isinstance(orig_node_id, int):
            orig_node_id = id(orig_node_id)
        return fast.Num(id(self._orig_to_copy_mapping[orig_node_id]))

    def _make_tuple_event_for(self, node: ast.AST, event: TraceEvent, orig_node_id=None, **kwargs):
        with fast.location_of(node):
            tuple_node = fast.Tuple(
                [
                    fast.Call(
                        func=self._emitter_ast(),
                        args=[event.to_ast(), self._get_copy_id_ast(orig_node_id or node)],
                        keywords=[] if len(kwargs) == 0 else fast.kwargs(**kwargs),
                    ),
                    node,
                ],
                ast.Load()
            )
            slc: Union[ast.Constant, ast.Num, ast.Index] = fast.Num(1)
            if sys.version_info < (3, 9):
                slc = fast.Index(slc)
            return fast.Subscript(tuple_node, slc, ast.Load())

    def visit(self, node: ast.AST):
        ret = super().visit(node)
        if isinstance(node, ast.stmt):
            # we haven't inserted statements yet, and StatementInserter needs the previous ids to be identical
            assert ret is node
        return ret

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

    def visit_Subscript(self, node: ast.Subscript, call_context=False):
        with fast.location_of(node.slice if hasattr(node.slice, 'lineno') else node.value):
            attr_or_sub = _maybe_convert_ast_subscript(node.slice)
            if isinstance(attr_or_sub, (ast.Slice, ast.ExtSlice)):
                elts = attr_or_sub.elts if isinstance(attr_or_sub, ast.Tuple) else attr_or_sub.dims  # type: ignore
                elts = [_maybe_convert_ast_subscript(elt) for elt in elts]
                attr_or_sub = fast.Tuple(elts, ast.Load())
        return self.visit_Attribute_or_Subscript(node, cast(ast.expr, attr_or_sub), call_context=call_context)

    def _maybe_wrap_symbol_in_before_after_tracing(
        self, node, call_context=False, orig_node_id=None, begin_kwargs=None, end_kwargs=None
    ):
        if self._inside_attrsub_load_chain:
            return node
        orig_node = node
        orig_node_id = orig_node_id or id(orig_node)
        begin_kwargs = begin_kwargs or {}
        end_kwargs = end_kwargs or {}

        ctx = getattr(orig_node, 'ctx', ast.Load())
        is_load = isinstance(ctx, ast.Load)

        with fast.location_of(node):
            begin_kwargs['ret'] = self._get_copy_id_ast(orig_node_id)
            if is_load:
                end_ret = orig_node
            elif isinstance(orig_node, (ast.Attribute, ast.Subscript)):
                end_ret = orig_node.value
            else:
                raise TypeError('Unsupported node type for before / after symbol tracing: %s', type(orig_node))
            end_kwargs['ret'] = end_ret
            end_kwargs['ctx'] = fast.Str(ctx.__class__.__name__)
            end_kwargs['call_context'] = fast.NameConstant(call_context)
            node = fast.Call(
                func=self._emitter_ast(),
                args=[
                    TraceEvent.after_complex_symbol.to_ast(),
                    fast.Call(
                        # this will return the node id
                        func=self._emitter_ast(),
                        args=[TraceEvent.before_complex_symbol.to_ast(), self._get_copy_id_ast(orig_node_id)],
                        keywords=fast.kwargs(**begin_kwargs),
                    )
                ],
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
                    logger.error(
                        'Symbol tracing stores unsupported for node %s with type %s', orig_node, type(orig_node)
                    )
                    assert False
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
                slice_val = resolve_slice_to_constant(node)
                if isinstance(slice_val, ast.Name):
                    # TODO: this should be more general than just simple ast.Name subscripts
                    subscript_name = slice_val.id

            with self.attrsub_context(node):
                node.value = fast.Call(
                    func=self._emitter_ast(),
                    args=[
                        TraceEvent.subscript.to_ast() if isinstance(node, ast.Subscript) else TraceEvent.attribute.to_ast(),
                        self._get_copy_id_ast(node.value)
                    ],
                    keywords=fast.kwargs(
                        ret=self.visit(node.value),
                        attr_or_subscript=attr_or_sub,
                        ctx=fast.Str(node.ctx.__class__.__name__),
                        call_context=fast.NameConstant(call_context),
                        top_level_node_id=self._get_copy_id_ast(self._top_level_node_for_symbol),
                        subscript_name=(
                            fast.NameConstant(subscript_name) if subscript_name is None else fast.Str(subscript_name)
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
                    visited_maybe_kwarg = self.visit(maybe_kwarg)
                with self.attrsub_context(None):
                    new_arg_value = cast(ast.expr, fast.Call(
                        func=self._emitter_ast(),
                        args=[TraceEvent.argument.to_ast(), self._get_copy_id_ast(maybe_kwarg)],
                        keywords=fast.kwargs(
                            ret=visited_maybe_kwarg,
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
        with fast.location_of(node.func):
            node.func = fast.Call(
                func=self._emitter_ast(),
                args=[TraceEvent.before_call.to_ast(), self._get_copy_id_ast(orig_node_id)],
                keywords=fast.kwargs(
                    ret=node.func,
                    call_node_id=self._get_copy_id_ast(orig_node_id),
                ),
            )

        # f(a, b, ..., c) -> trace(f(a, b, ..., c), 'exit argument list')
        with fast.location_of(node):
            node = fast.Call(
                func=self._emitter_ast(),
                args=[TraceEvent.after_call.to_ast(), self._get_copy_id_ast(orig_node_id)],
                keywords=fast.kwargs(
                    ret=node,
                    call_node_id=self._get_copy_id_ast(orig_node_id),
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
            node.value = self._make_tuple_event_for(
                self.visit(node.value), TraceEvent.before_assign_rhs, orig_node_id=orig_value_id
            )
            node.value = fast.Call(
                func=self._emitter_ast(),
                args=[TraceEvent.after_assign_rhs.to_ast(), self._get_copy_id_ast(orig_value_id)],
                keywords=fast.kwargs(ret=node.value),
            )
        return node

    def visit_Lambda(self, node: ast.Lambda):
        assert isinstance(getattr(node, 'ctx', ast.Load()), ast.Load)
        visited = self.generic_visit(node)
        with fast.location_of(node):
            subscripted_node = self._make_tuple_event_for(
                visited, TraceEvent.before_lambda, orig_node_id=id(node)
            )
            return fast.Call(
                func=self._emitter_ast(),
                args=[TraceEvent.after_lambda.to_ast(), self._get_copy_id_ast(node)],
                keywords=fast.kwargs(ret=subscripted_node),
            )

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
        maybe_visited: ast.AST = node
        if should_inner_visit:
            maybe_visited = self.generic_visit(node)
        if not isinstance(getattr(node, 'ctx', ast.Load()), ast.Load):
            return maybe_visited
        with fast.location_of(node):
            subscripted_node = self._make_tuple_event_for(
                maybe_visited, self._ast_container_to_literal_trace_evt(node, before=True), orig_node_id=id(node)
            )
            return fast.Call(
                func=self._emitter_ast(),
                args=[
                    self._ast_container_to_literal_trace_evt(node, before=False).to_ast(),
                    self._get_copy_id_ast(node)
                ],
                keywords=fast.kwargs(ret=subscripted_node),
            )

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
        for i, elt in enumerate(node.elts):
            if isinstance(elt, ast.Starred):
                # TODO: trace starred elts too
                saw_starred = True
                traced_elts.append(elt)
                continue
            elif not is_load:
                traced_elts.append(self.visit(elt))
                continue
            with fast.location_of(elt):
                traced_elts.append(fast.Call(
                    func=self._emitter_ast(),
                    args=[
                        self._ast_container_to_elt_trace_evt(node).to_ast(),
                        self._get_copy_id_ast(elt),
                    ],
                    keywords=fast.kwargs(
                        ret=self.visit(elt),
                        index=fast.NameConstant(None) if saw_starred else fast.Num(i),
                        container_node_id=self._get_copy_id_ast(node),
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
                    traced_keys.append(fast.Call(
                        func=self._emitter_ast(),
                        args=[TraceEvent.dict_key.to_ast(), self._get_copy_id_ast(k)],
                        keywords=fast.kwargs(
                            ret=self.visit(k),
                            value_node_id=self._get_copy_id_ast(v),
                            dict_node_id=self._get_copy_id_ast(node),
                        )
                    ))
            with fast.location_of(v):
                if is_dict_unpack:
                    key_node_id_ast = fast.NameConstant(None)
                else:
                    key_node_id_ast = self._get_copy_id_ast(k)
                traced_values.append(fast.Call(
                    func=self._emitter_ast(),
                    args=[TraceEvent.dict_value.to_ast(), self._get_copy_id_ast(v)],
                    keywords=fast.kwargs(
                        ret=self.visit(v),
                        key_node_id=key_node_id_ast,
                        dict_node_id=self._get_copy_id_ast(node),
                    )
                ))
        node.keys = traced_keys
        node.values = traced_values
        return self.visit_literal(node, should_inner_visit=False)

    def visit_Return(self, node: ast.Return):
        if node.value is None:
            return node
        with fast.location_of(node):
            node.value = fast.Call(
                func=self._emitter_ast(),
                args=[TraceEvent.after_return.to_ast(), self._get_copy_id_ast(node.value)],
                keywords=fast.kwargs(
                    ret=self._make_tuple_event_for(
                        self.visit(node.value),
                        TraceEvent.before_return,
                        orig_node_id=id(node.value),
                    ),
                ),
            )
        return node

    def visit_Delete(self, node: ast.Delete):
        ret = cast(ast.Delete, self.generic_visit(node))
        for target in ret.targets:
            target.ctx = ast.Del()  # type: ignore
        return ret
