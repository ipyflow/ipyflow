# -*- coding: utf-8 -*-
import ast
from contextlib import contextmanager
import logging
from typing import cast, TYPE_CHECKING

from nbsafety.analysis.attr_symbols import GetAttrSubSymbols
from nbsafety.tracing.hooks import TracingHook
from nbsafety.utils import fast

if TYPE_CHECKING:
    from typing import Dict, List, Set, Union


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class AstEavesdropper(ast.NodeTransformer):
    def __init__(self, orig_to_copy_mapping: 'Dict[int, ast.AST]'):
        self._orig_to_copy_mapping = orig_to_copy_mapping
        self._inside_attrsub_load_chain = False

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

            extra_args: 'List[ast.AST]' = []
            if isinstance(node.value, ast.Name):
                extra_args = [fast.Str(node.value.id)]

            with self.attrsub_load_context():
                node.value = fast.Call(
                    func=fast.Name(TracingHook.attrsub_tracer.value, ast.Load()),
                    args=[
                             self.visit(node.value),
                             attr_or_sub,
                             fast.NameConstant(is_subscript),
                             fast.Str(node.ctx.__class__.__name__),
                             fast.NameConstant(call_context),
                         ] + extra_args,
                    keywords=[]
                )
        # end fast.location_of(node.value)
        if not self._inside_attrsub_load_chain and is_load:
            with fast.location_of(node):
                return fast.Call(
                    func=fast.Name(TracingHook.end_tracer.value, ast.Load()),
                    args=[fast.Num(id(self._orig_to_copy_mapping[id(node)])), node, fast.NameConstant(call_context)],
                    keywords=[]
                )
        return node

    def _get_replacement_args(self, args, should_record, keywords):
        replacement_args = []
        for arg in args:
            if keywords:
                maybe_kwarg = getattr(arg, 'value')
            else:
                maybe_kwarg = arg
            chain = GetAttrSubSymbols()(maybe_kwarg)
            statically_resolvable = []
            with fast.location_of(maybe_kwarg):
                for sym in chain.symbols:
                    # TODO: only handles attributes properly; subscripts will break
                    if not isinstance(sym, str):
                        break
                    statically_resolvable.append(ast.Str(sym))
                statically_resolvable = fast.Tuple(elts=statically_resolvable, ctx=ast.Load())
                with self.attrsub_load_context(False):
                    visited_maybe_kwarg = self.visit(maybe_kwarg)
                argrecord_args = [visited_maybe_kwarg, statically_resolvable]
                if should_record:
                    with self.attrsub_load_context(False):
                        new_arg_value = cast(ast.expr, fast.Call(
                            func=fast.Name(TracingHook.arg_recorder.value, ast.Load()),
                            args=argrecord_args,
                            keywords=[]
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
        # we need to push current active scope before processing the args and pop after
        # (pop happens on function return as opposed to in tracer)
        with fast.location_of(node.func):
            node.func = fast.Call(
                func=fast.Name(TracingHook.scope_pusher.value, ast.Load()),
                args=[node.func],
                keywords=[],
            )

        with fast.location_of(node):
            node = fast.Call(
                func=fast.Name(TracingHook.scope_popper.value, ast.Load()),
                args=[node, fast.NameConstant(is_attrsub)],
                keywords=[]
            )

        if self._inside_attrsub_load_chain or not is_attrsub:
            return node

        with fast.location_of(node):
            return fast.Call(
                func=fast.Name(TracingHook.end_tracer.value, ast.Load()),
                args=[fast.Num(id(self._orig_to_copy_mapping[orig_node_id])), node, fast.NameConstant(True)],
                keywords=[]
            )

    def visit_Assign(self, node: ast.Assign):
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            return self.generic_visit(node)

        new_targets = []
        for target in node.targets:
            new_targets.append(self.visit(target))
        node.targets = cast('List[ast.expr]', new_targets)
        with fast.location_of(node.value):
            node.value = fast.Call(
                func=fast.Name(TracingHook.literal_tracer.value, ast.Load()),
                args=[node.value],
                keywords=[],
            )
        return node
