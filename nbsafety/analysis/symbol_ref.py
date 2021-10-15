# -*- coding: future_annotations -*-
import ast
from typing import cast, Union, TYPE_CHECKING

from nbsafety.analysis.resolved_symbols import ResolvedDataSymbol
from nbsafety.singletons import nbs
from nbsafety.utils import CommonEqualityMixin

if TYPE_CHECKING:
    from typing import Any, Generator, List, Optional, Sequence, Tuple, Union
    from nbsafety.data_model.data_symbol import Scope
    from nbsafety.types import SupportedIndexType


class Atom(CommonEqualityMixin):
    def __init__(
        self,
        value: SupportedIndexType,
        is_callpoint: bool = False,
        is_subscript: bool = False,
        is_reactive: bool = False,
    ):
        self.value = value
        self.is_callpoint = is_callpoint
        self.is_subscript = is_subscript
        self.is_reactive = is_reactive

    def nonreactive(self) -> Atom:
        return self.__class__(self.value, is_callpoint=self.is_callpoint, is_subscript=self.is_subscript, is_reactive=False)

    def reactive(self) -> Atom:
        return self.__class__(self.value, is_callpoint=self.is_callpoint, is_subscript=self.is_subscript, is_reactive=True)

    def __hash__(self):
        return hash((self.value, self.is_callpoint, self.is_subscript, self.is_reactive))

    def __repr__(self):
        return repr(str(self))

    def __str__(self):
        return self.value + ('(...)' if self.is_callpoint else '')


class SymbolRef(CommonEqualityMixin):
    def __init__(self, symbols: Union[str, Atom, Sequence[Atom]]):
        # FIXME: each symbol should distinguish between attribute and subscript
        # FIXME: bumped in priority 2021/09/07
        if isinstance(symbols, str):
            symbols = [Atom(symbols)]
        elif isinstance(symbols, Atom):
            symbols = [symbols]
        self.chain: Tuple[Atom, ...] = tuple(symbols)

    def __hash__(self):
        return hash(self.chain)

    def __repr__(self):
        return repr(self.chain)

    def nonreactive(self) -> SymbolRef:
        return self.__class__([atom.nonreactive() for atom in self.chain])

    def gen_resolved_symbols(
        self,
        scope: Scope,
        only_yield_final_symbol: bool,
        yield_all_intermediate_symbols: bool = False,
    ) -> Generator[ResolvedDataSymbol, None, None]:
        assert not (only_yield_final_symbol and yield_all_intermediate_symbols)
        dsym, atom, next_atom = None, None, None
        reactive_seen = False
        for dsym, atom, next_atom in scope.gen_data_symbols_for_attrsub_chain(self):
            reactive_seen = reactive_seen or atom.is_reactive
            yield_all_intermediate_symbols = yield_all_intermediate_symbols or reactive_seen
            if reactive_seen and not atom.is_reactive:
                atom = atom.reactive()
            if yield_all_intermediate_symbols:
                # TODO: only use this branch one staleness checker can be smarter about liveness timestamps.
                #  Right now, yielding the intermediate elts of the chain will yield false positives in the
                #  event of namespace stale children.
                yield ResolvedDataSymbol(dsym, atom, next_atom)
        if not yield_all_intermediate_symbols and dsym is not None:
            if next_atom is None or not only_yield_final_symbol:
                yield ResolvedDataSymbol(dsym, atom, next_atom)


class LiveSymbolRef(CommonEqualityMixin):
    def __init__(self, ref: SymbolRef, timestamp: int) -> None:
        self.ref = ref
        self.timestamp = timestamp

    def __hash__(self):
        return hash((self.ref, self.timestamp))

    def gen_resolved_symbols(
        self, scope: Scope, only_yield_final_symbol: bool, yield_all_intermediate_symbols: bool = False
    ):
        for resolved_sym in self.ref.gen_resolved_symbols(
            scope, only_yield_final_symbol, yield_all_intermediate_symbols=yield_all_intermediate_symbols
        ):
            resolved_sym.liveness_timestamp = self.timestamp
            yield resolved_sym


class GetSymbolRefs(ast.NodeVisitor):
    def __init__(self):
        self.symbol_chain: List[Atom] = []

    def __call__(self, node: Union[ast.Attribute, ast.Subscript, ast.Call, ast.Name]) -> SymbolRef:
        self.visit(node)
        self.symbol_chain.reverse()
        return SymbolRef(self.symbol_chain)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Attribute):
            self.symbol_chain.append(Atom(node.func.attr, is_callpoint=True, is_reactive=id(node) in nbs().reactive_attribute_node_ids))
            self.visit(node.func.value)
        elif isinstance(node.func, ast.Subscript):
            if isinstance(node.func.slice, ast.Constant):
                self.symbol_chain.append(Atom(str(node.func.slice.value), is_callpoint=True))
            elif isinstance(node.func.slice, ast.Index) and isinstance(node.func.slice.value, (ast.Str, ast.Num)):
                if isinstance(node.func.slice.value, ast.Str):
                    self.symbol_chain.append(Atom(node.func.slice.value.s, is_callpoint=True, is_subscript=True))
                else:
                    self.symbol_chain.append(Atom(str(node.func.slice.value.n), is_callpoint=True, is_subscript=True))
                self.visit(node.func.value)
        elif isinstance(node.func, ast.Name):
            self.visit(node.func)
            self.symbol_chain[-1].is_callpoint = True
        elif isinstance(node.func, ast.Call):
            # TODO: handle this case too, e.g. f.g()().h
            pass
        else:
            raise TypeError('invalid type for node.func %s' % node.func)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.symbol_chain.append(Atom(node.attr, is_reactive=id(node) in nbs().reactive_attribute_node_ids))
        self.visit(node.value)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        resolved = resolve_slice_to_constant(node)
        if resolved is not None:
            if isinstance(resolved, ast.Name):
                # FIXME: hack to make the static checker stop here
                # In the future, it should try to attempt to resolve
                # the value of the ast.Name node
                self.symbol_chain.append(Atom(resolved.id, is_subscript=True))
            else:
                self.symbol_chain.append(Atom(resolved, is_subscript=True))
        self.visit(node.value)

    def visit_Name(self, node: ast.Name) -> None:
        self.symbol_chain.append(Atom(node.id, is_reactive=id(node) in nbs().reactive_variable_node_ids))

    def generic_visit(self, node):
        # raise ValueError('we should never get here: %s' % node)
        # give up
        return


def get_attrsub_symbol_chain(maybe_node: Union[str, ast.Name, ast.Attribute, ast.Subscript, ast.Call]) -> SymbolRef:
    if isinstance(maybe_node, (ast.Name, ast.Attribute, ast.Subscript, ast.Call)):
        node = maybe_node
    else:
        node = cast('Union[ast.Name, ast.Attribute, ast.Subscript, ast.Call]',
                    cast(ast.Expr, ast.parse(maybe_node).body[0]).value)
    if not isinstance(node, (ast.Name, ast.Attribute, ast.Subscript, ast.Call)):
        raise TypeError('invalid type for node %s' % node)
    return GetSymbolRefs()(node)


def resolve_slice_to_constant(node: ast.Subscript) -> Optional[Union[SupportedIndexType, ast.Name]]:
    """
    Version-independent way to get at the slice data
    """
    if isinstance(node.slice, ast.Index):
        slice = node.slice.value  # type: ignore
    else:
        slice = node.slice  # type: ignore

    if isinstance(slice, ast.Tuple):
        elts: Any = []
        for v in slice.elts:
            if isinstance(v, ast.Num):
                elts.append(v.n)
            elif isinstance(v, ast.Str):
                elts.append(v.s)
            elif isinstance(v, ast.Constant):
                elts.append(v.value)
            else:
                return None
        return tuple(elts)  # type: ignore

    negate = False
    if isinstance(slice, ast.UnaryOp) and isinstance(slice.op, ast.USub):
        negate = True
        slice = slice.operand

    if isinstance(slice, ast.Name):
        return slice

    if not isinstance(slice, (ast.Constant, ast.Str, ast.Num)):
        return None

    if isinstance(slice, ast.Constant):
        slice = slice.value
    elif isinstance(slice, ast.Num):  # pragma: no cover
        slice = slice.n  # type: ignore
        if not isinstance(slice, int):
            return None
    elif isinstance(slice, ast.Str):  # pragma: no cover
        slice = slice.s  # type: ignore
    else:
        return None

    if isinstance(slice, int) and negate:
        slice = -slice  # type: ignore
    return slice  # type: ignore
