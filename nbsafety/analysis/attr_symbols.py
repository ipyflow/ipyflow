# -*- coding: utf-8 -*-
import ast
from typing import cast, Union, TYPE_CHECKING

from ..utils.mixins import CommonEqualityMixin

if TYPE_CHECKING:
    from typing import List, Tuple, Union


class CallPoint(CommonEqualityMixin):
    def __init__(self, symbol: str):
        self.symbol = symbol

    def __hash__(self):
        return hash(self.symbol)

    def __repr__(self):
        return repr(str(self))

    def __str__(self):
        return self.symbol + '(...)'


class AttrSubSymbolChain(CommonEqualityMixin):
    def __init__(self, symbols: 'List[Union[str, CallPoint]]'):
        self.symbols: 'Tuple[Union[str, CallPoint], ...]' = tuple(symbols)
        self.call_points = tuple(filter(lambda x: isinstance(x, CallPoint), self.symbols))

    def __hash__(self):
        return hash(self.symbols)

    def __repr__(self):
        return repr(self.symbols)


class GetAttrSubSymbols(ast.NodeVisitor):
    def __init__(self):
        self.symbol_chain: List[Union[str, CallPoint]] = []

    def __call__(self, node: 'Union[ast.Attribute, ast.Subscript, ast.Call]') -> 'AttrSubSymbolChain':
        self.visit(node)
        self.symbol_chain.reverse()
        return AttrSubSymbolChain(self.symbol_chain)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute):
            self.symbol_chain.append(CallPoint(node.func.attr))
            self.visit(node.func.value)
        elif isinstance(node.func, ast.Subscript):
            self.symbol_chain.append(CallPoint(node.func.slice))
            self.visit(node.func.value)
        elif isinstance(node.func, ast.Name):
            self.symbol_chain.append(CallPoint(node.func.id))
        else:
            raise TypeError('invalid type for node.func %s' % node.func)

    def visit_Attribute(self, node):
        self.symbol_chain.append(node.attr)
        self.visit(node.value)

    def visit_Subscript(self, node):
        node_slice = node.slice
        if isinstance(node_slice, ast.Index):
            slice_index = node_slice.value
            if isinstance(slice_index, ast.Str):
                self.symbol_chain.append(slice_index.s)
            elif isinstance(slice_index, ast.Num):
                self.symbol_chain.append(slice_index.n)
            elif isinstance(slice_index, ast.Name):
                # FIXME: hack to make the static checker stop here
                # In the future, it should try to attempt to resolve
                # the value of the ast.Name node
                self.symbol_chain.append(CallPoint(slice_index.id))
            else:
                # give up
                pass
                # raise TypeError('unexpected type for node.slice %s' % node_slice)
        else:
            # give up
            pass
        self.visit(node.value)

    def visit_Name(self, node):
        self.symbol_chain.append(node.id)

    def visit_Str(self, node):
        return

    def generic_visit(self, node):
        # raise ValueError('we should never get here: %s' % node)
        # give up
        return


def get_attrsub_symbol_chain(maybe_node: Union[str, ast.Attribute, ast.Subscript, ast.Call]) -> AttrSubSymbolChain:
    if isinstance(maybe_node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = maybe_node
    else:
        node = cast(Union[ast.Attribute, ast.Subscript, ast.Call], cast(ast.Expr, ast.parse(maybe_node).body[0]).value)
    if not isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        raise TypeError('invalid type for node %s' % node)
    return GetAttrSubSymbols()(node)
