# -*- coding: utf-8 -*-
import ast
from contextlib import contextmanager
import logging
from typing import cast, TYPE_CHECKING
import sys

from nbsafety.tracing.trace_events import TraceEvent, EMIT_EVENT
from nbsafety.utils import fast

if TYPE_CHECKING:
    from typing import Dict, List, Set, Union


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class AstEavesdropper(ast.NodeTransformer):
    def __init__(self, orig_to_copy_mapping: 'Dict[int, ast.AST]'):
        self._orig_to_copy_mapping = orig_to_copy_mapping
        self._inside_attrsub_load_chain = False

    def _emitter_ast(self):
        return fast.Name(EMIT_EVENT, ast.Load())

    def _get_copy_id_ast(self, orig_node_id: 'Union[int, ast.AST]'):
        if not isinstance(orig_node_id, int):
            orig_node_id = id(orig_node_id)
        return fast.Num(id(self._orig_to_copy_mapping[orig_node_id]))

    def visit(self, node: 'ast.AST'):
        ret = super().visit(node)
        if isinstance(node, ast.stmt):
            # we haven't inserted statements yet, and StatementInserter needs the previous ids to be identical
            assert ret is node
        return ret

    @contextmanager
    def attrsub_load_context(self, override=True):
        old = self._inside_attrsub_load_chain
        self._inside_attrsub_load_chain = override
        yield
        self._inside_attrsub_load_chain = old

    def visit_Attribute(self, node: 'ast.Attribute', call_context=False):
        return self.visit_Attribute_or_Subscript(node, call_context)

    def visit_Subscript(self, node: 'ast.Subscript', call_context=False):
        return self.visit_Attribute_or_Subscript(node, call_context)

    def visit_Attribute_or_Subscript(self, node: 'Union[ast.Attribute, ast.Subscript]', call_context=False):
        with fast.location_of(node.value):
            is_load = isinstance(node.ctx, ast.Load)
            is_subscript = isinstance(node, ast.Subscript)
            # TODO: expand beyond simple slices
            if is_subscript:
                sub_node = cast(ast.Subscript, node)
                if isinstance(sub_node.slice, ast.Index):
                    attr_or_sub = sub_node.slice.value  # type: ignore
                    # ast.copy_location(attr_or_sub, sub_node.slice.value)
                    # if isinstance(attr_or_sub, ast.Str):
                    #     attr_or_sub = attr_or_sub.s
                    # elif isinstance(attr_or_sub, ast.Num):
                    #     attr_or_sub = attr_or_sub.n
                    # else:
                    #     logger.debug('unimpled index: %s', attr_or_sub)
                    #     return node
                elif isinstance(sub_node.slice, ast.Constant):
                    # Python > 3.8 doesn't use ast.Index for constant slices
                    attr_or_sub = sub_node.slice
                else:
                    logger.debug('unimpled slice: %s', sub_node.slice)
                    return node
                # elif isinstance(sub_node.slice, ast.Slice):
                #     raise ValueError('unimpled slice: %s' % sub_node.slice)
                # elif isinstance(sub_node.slice, ast.ExtSlice):
                #     raise ValueError('unimpled slice: %s' % sub_node.slice)
                # else:
                #     raise ValueError('unexpected slice: %s' % sub_node.slice)
            else:
                attr_node = cast(ast.Attribute, node)
                attr_or_sub = fast.Str(attr_node.attr)

            extra_args: 'List[ast.keyword]' = []
            if isinstance(node.value, ast.Name):
                extra_args = fast.kwargs(name=fast.Str(node.value.id))

            with self.attrsub_load_context():
                node.value = fast.Call(
                    func=self._emitter_ast(),
                    args=[
                        TraceEvent.subscript.to_ast() if is_subscript else TraceEvent.attribute.to_ast(),
                        self._get_copy_id_ast(node.value)
                    ],
                    keywords=fast.kwargs(
                        obj=self.visit(node.value),
                        attr_or_sub=attr_or_sub,
                        ctx=fast.Str(node.ctx.__class__.__name__),
                        call_context=fast.NameConstant(call_context),
                    ) + extra_args
                )
        # end fast.location_of(node.value)
        if not self._inside_attrsub_load_chain and is_load:
            with fast.location_of(node):
                return fast.Call(
                    func=self._emitter_ast(),
                    args=[TraceEvent.after_attrsub_chain.to_ast(), self._get_copy_id_ast(node)],
                    keywords=fast.kwargs(obj=node, call_context=fast.NameConstant(call_context)),
                )
        return node

    def _get_replacement_args(self, args, should_record: bool, keywords: bool):
        replacement_args = []
        for arg in args:
            if keywords:
                maybe_kwarg = getattr(arg, 'value')
            else:
                maybe_kwarg = arg
            with fast.location_of(maybe_kwarg):
                with self.attrsub_load_context(False):
                    visited_maybe_kwarg = self.visit(maybe_kwarg)
                if should_record:
                    with self.attrsub_load_context(False):
                        new_arg_value = cast(ast.expr, fast.Call(
                            func=self._emitter_ast(),
                            args=[TraceEvent.argument.to_ast(), self._get_copy_id_ast(maybe_kwarg)],
                            keywords=fast.kwargs(obj=visited_maybe_kwarg),
                        ))
                else:
                    new_arg_value = visited_maybe_kwarg
            if keywords:
                setattr(arg, 'value', new_arg_value)
            else:
                arg = new_arg_value
            replacement_args.append(arg)
        return replacement_args

    def visit_Call(self, node: ast.Call):
        orig_node_id = id(node)
        is_attrsub = False
        if isinstance(node.func, (ast.Attribute, ast.Subscript)):
            is_attrsub = True
            with self.attrsub_load_context():
                node.func = self.visit_Attribute_or_Subscript(node.func, call_context=True)

            # TODO: need a way to rewrite ast of attribute and subscript args,
            #  and to process these separately from outer rewrite

        node.args = self._get_replacement_args(node.args, is_attrsub, False)
        node.keywords = self._get_replacement_args(node.keywords, is_attrsub, True)

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
                args=[TraceEvent.before_arg_list.to_ast(), self._get_copy_id_ast(node.func)],
                keywords=fast.kwargs(obj=node.func),
            )

        # f(a, b, ..., c) -> trace(f(a, b, ..., c), 'exit argument list')
        with fast.location_of(node):
            node = fast.Call(
                func=self._emitter_ast(),
                args=[TraceEvent.after_arg_list.to_ast(), self._get_copy_id_ast(node)],
                keywords=fast.kwargs(
                    obj=node,
                    is_attrsub=fast.NameConstant(is_attrsub),
                    inside_chain=fast.NameConstant(self._inside_attrsub_load_chain),
                ),
            )

        if self._inside_attrsub_load_chain or not is_attrsub:
            return node

        with fast.location_of(node):
            return fast.Call(
                func=self._emitter_ast(),
                args=[TraceEvent.after_attrsub_chain.to_ast(), self._get_copy_id_ast(orig_node_id)],
                keywords=fast.kwargs(obj=node, call_context=fast.NameConstant(True)),
            )

    def visit_Assign(self, node: ast.Assign):
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            return self.generic_visit(node)

        orig_node_value = id(node.value)
        new_targets = []
        for target in node.targets:
            new_targets.append(self.visit(target))
        node.targets = cast('List[ast.expr]', new_targets)
        with fast.location_of(node.value):
            # TODO: replace 42 with start literal tracer
            node.value = fast.Tuple([fast.Call(
                func=self._emitter_ast(),
                args=[TraceEvent.before_literal.to_ast(), self._get_copy_id_ast(orig_node_value)],
                keywords=[],
            ), node.value], ast.Load())
            slc: 'Union[ast.Constant, ast.Num, ast.Index]' = fast.Num(1)
            if sys.version_info < (3, 9):
                slc = fast.Index(slc)
            node.value = fast.Subscript(node.value, slc, ast.Load())
            node.value = fast.Call(
                func=self._emitter_ast(),
                args=[TraceEvent.after_literal.to_ast(), self._get_copy_id_ast(orig_node_value)],
                keywords=fast.kwargs(obj=node.value),
            )
        return node
