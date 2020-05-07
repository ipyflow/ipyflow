# -*- coding: utf-8 -*-
import ast
import builtins
from contextlib import contextmanager
import logging
from typing import TYPE_CHECKING

from ..data_cell import DataCell

if TYPE_CHECKING:
    from typing import Dict, List, Set, Tuple, Union
    from ..scope import Scope

logger = logging.getLogger(__name__)


class AttributeTracingManager(object):
    def __init__(self, namespaces: 'Dict[int, Scope]', active_scope: 'Scope'):
        self.namespaces = namespaces
        self.original_active_scope = active_scope
        self.active_scope = active_scope
        self.start_tracer_name = '_ATTR_TRACER_START'
        self.end_tracer_name = '_ATTR_TRACER_END'
        setattr(builtins, self.start_tracer_name, self.attribute_tracer)
        setattr(builtins, self.end_tracer_name, self.expr_tracer)
        self.ast_transformer = AttributeTracingNodeTransformer(self.start_tracer_name, self.end_tracer_name)
        self.loaded_data_cells: Set[DataCell] = set()
        self.stored_scope_qualified_names: Set[Tuple[Scope, str]] = set()
        self.aug_stored_scope_qualified_names: Set[Tuple[Scope, str]] = set()
        self.stack: List[Tuple[Set[Tuple[Scope, str]], Set[Tuple[Scope, str]], Scope, Scope]] = []

    def __del__(self):
        if hasattr(builtins, self.start_tracer_name):
            delattr(builtins, self.start_tracer_name)
        if hasattr(builtins, self.end_tracer_name):
            delattr(builtins, self.end_tracer_name)

    def push_stack(self, new_scope: 'Scope'):
        self.stack.append((
            self.stored_scope_qualified_names,
            self.aug_stored_scope_qualified_names,
            self.active_scope,
            self.original_active_scope,
        ))
        self.stored_scope_qualified_names = set()
        self.aug_stored_scope_qualified_names = set()
        self.original_active_scope = new_scope
        self.active_scope = new_scope

    def pop_stack(self):
        (
            self.stored_scope_qualified_names,
            self.aug_stored_scope_qualified_names,
            self.active_scope,
            self.original_active_scope,
        ) = self.stack.pop()

    @staticmethod
    def debug_attribute_tracer(obj, attr, ctx):
        logger.debug('%s attr %s of obj %s', ctx, attr, obj)
        return obj

    def attribute_tracer(self, obj, attr, ctx, override_active_scope):
        obj_id = id(obj)
        scope = self.namespaces.get(obj_id, None)
        # print('%s attr %s of obj %s' % (ctx, attr, obj))
        if scope is None:
            class_scope = self.namespaces.get(id(obj.__class__), None)
            if class_scope is not None:
                # print('found class scope %s containing %s' % (class_scope, class_scope.data_cell_by_name.keys()))
                scope = class_scope.clone()
                self.namespaces[obj_id] = scope
            # else:
            #     print('no scope for class', obj.__class__)
        # print('new active scope', scope)
        if override_active_scope:
            self.active_scope = scope
        if scope is None:
            return obj
        if ctx == 'Load':
            data_cell = scope.data_cell_by_name.get(attr, None)
            if data_cell is None:
                data_cell = DataCell(attr)
                scope.data_cell_by_name[attr] = data_cell
            self.loaded_data_cells.add(data_cell)
        if ctx == 'Store':
            self.stored_scope_qualified_names.add((scope, attr))
        if ctx == 'AugStore':
            self.aug_stored_scope_qualified_names.add((scope, attr))
        return obj

    def expr_tracer(self, obj):
        # print('reset active scope to', self.original_active_scope)
        self.active_scope = self.original_active_scope
        return obj

    def reset(self):
        self.loaded_data_cells = set()
        self.stored_scope_qualified_names = set()
        self.aug_stored_scope_qualified_names = set()
        self.active_scope = self.original_active_scope


class AttributeTracingNodeTransformer(ast.NodeTransformer):
    def __init__(self, start_tracer: str, end_tracer: str):
        self.start_tracer = start_tracer
        self.end_tracer = end_tracer
        self.inside_attr_load_chain = False

    @contextmanager
    def attribute_load_context(self, override=True):
        old = self.inside_attr_load_chain
        self.inside_attr_load_chain = override or old
        yield
        self.inside_attr_load_chain = old

    def visit_Attribute(self, node: 'ast.Attribute'):
        override_active_scope = isinstance(node.ctx, ast.Load)
        override_active_scope_arg = ast.Constant(override_active_scope)
        ast.copy_location(override_active_scope_arg, node)
        with self.attribute_load_context(override_active_scope):
            replacement_value = ast.Call(
                func=ast.Name(self.start_tracer, ctx=ast.Load()),
                args=[
                    self.visit(node.value), ast.Str(node.attr),
                    ast.Str(node.ctx.__class__.__name__), override_active_scope_arg
                ],
                keywords=[]
            )
        ast.copy_location(replacement_value, node.value)
        node.value = replacement_value
        new_node: Union[ast.Attribute, ast.Call] = node
        if not self.inside_attr_load_chain and override_active_scope:
            new_node = ast.Call(
                func=ast.Name(self.end_tracer, ctx=ast.Load()),
                args=[node],
                keywords=[]
            )
        return new_node

    def visit_Call(self, node: ast.Call):
        if not isinstance(node.func, ast.Attribute):
            return node
        assert isinstance(node.func.ctx, ast.Load)
        with self.attribute_load_context():
            node.func = self.visit_Attribute(node.func)
        if self.inside_attr_load_chain:
            return node
        replacement_node = ast.Call(
            func=ast.Name(self.end_tracer, ctx=ast.Load()),
            args=[node],
            keywords=[]
        )
        ast.copy_location(replacement_node, node)
        return replacement_node
