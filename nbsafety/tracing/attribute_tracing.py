# -*- coding: utf-8 -*-
from __future__ import annotations
import ast
import builtins
import logging
from typing import TYPE_CHECKING

from ..data_cell import DataCell

if TYPE_CHECKING:
    from typing import Dict, Set, Tuple
    from ..scope import Scope


class AttributeTracingManager(object):
    def __init__(self, namespaces: Dict[int, Scope], active_scope: Scope):
        self.namespaces = namespaces
        self.original_active_scope = active_scope
        self.active_scope = active_scope
        self.attr_tracer_start_name = '_ATTR_TRACER_START'
        setattr(builtins, self.attr_tracer_start_name, self.attribute_tracer)
        self.ast_transformer = AttributeTracingNodeTransformer(self.attr_tracer_start_name)
        self.loaded_data_cells: Set[DataCell] = set()
        self.stored_scope_qualified_names: Set[Tuple[Scope, str]] = set()
        self.aug_stored_scope_qualified_names: Set[Tuple[Scope, str]] = set()
        self.stack = []

    def __del__(self):
        delattr(builtins, self.attr_tracer_start_name)

    def push_stack(self, new_scope: Scope):
        self.stack.append((self.stored_scope_qualified_names, self.aug_stored_scope_qualified_names, self.active_scope))
        self.stored_scope_qualified_names = set()
        self.aug_stored_scope_qualified_names = set()
        self.active_scope = new_scope

    def pop_stack(self):
        (
            self.stored_scope_qualified_names, self.aug_stored_scope_qualified_names, self.active_scope
        ) = self.stack.pop()

    @staticmethod
    def debug_attribute_tracer(obj, attr, ctx):
        logging.debug('%s attr %s of obj %s', ctx, attr, obj)
        return obj

    def attribute_tracer(self, obj, attr, ctx):
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

    def reset(self):
        self.active_scope = self.original_active_scope
        self.loaded_data_cells = set()
        self.stored_scope_qualified_names = set()
        self.aug_stored_scope_qualified_names = set()


class AttributeTracingNodeTransformer(ast.NodeTransformer):
    def __init__(self, start_tracer: str):
        self.start_tracer = start_tracer

    def visit_Attribute(self, node: ast.Attribute):
        replacement_value = ast.Call(
            func=ast.Name(self.start_tracer, ctx=ast.Load()),
            args=[self.visit(node.value), ast.Str(node.attr), ast.Str(node.ctx.__class__.__name__)],
            keywords=[]
        )
        ast.copy_location(replacement_value, node.value)
        node.value = replacement_value
        return node
