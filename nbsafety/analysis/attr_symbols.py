# -*- coding: utf-8 -*-
from __future__ import annotations
import ast
from typing import TYPE_CHECKING

from ..utils.mixins import CommonEqualityMixin

if TYPE_CHECKING:
    from typing import List, Optional, Tuple, Union


class GetAttributeSymbols(ast.NodeVisitor):
    def __init__(self):
        self.symbol_chain: List[Union[str, CallPoint]] = []

    def __call__(self, node: ast.Attribute) -> Tuple[List[Union[str, CallPoint]], List[CallPoint]]:
        self.visit(node)
        self.symbol_chain.reverse()
        call_points = list(filter(lambda x: isinstance(x, CallPoint), self.symbol_chain))
        return self.symbol_chain, call_points

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute):
            self.symbol_chain.append(CallPoint(node.func.attr))
            self.visit(node.func.value)
        elif isinstance(node.func, ast.Name):
            self.symbol_chain.append(CallPoint(node.func.id))
        else:
            raise TypeError('invalid type for node.func %s' % node.func)

    def visit_Attribute(self, node):
        self.symbol_chain.append(node.attr)
        self.visit(node.value)

    def visit_Name(self, node):
        self.symbol_chain.append(node.id)

    def generic_visit(self, node):
        raise ValueError('we should never get here')


def get_attribute_symbols(node: ast.Attribute) -> Tuple[List[Union[str, CallPoint]], List[CallPoint]]:
    return GetAttributeSymbols()(node)


class CallPoint(CommonEqualityMixin):
    def __init__(self, symbol: str, retval: Optional[int] = None):
        self.symbol = symbol
        self.retval = retval
