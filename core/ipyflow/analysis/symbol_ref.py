# -*- coding: utf-8 -*-
import ast
import logging
from typing import (
    TYPE_CHECKING,
    Any,
    Generator,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
    cast,
)

from ipyflow.analysis.resolved_symbols import ResolvedDataSymbol
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.singletons import flow, tracer
from ipyflow.types import SupportedIndexType
from ipyflow.utils import CommonEqualityMixin
from ipyflow.utils.ast_utils import subscript_to_slice

if TYPE_CHECKING:
    from ipyflow.data_model.data_symbol import DataSymbol, Scope


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


def resolve_slice_to_constant(
    node: ast.Subscript,
) -> Optional[Union[SupportedIndexType, ast.Name]]:
    """
    Version-independent way to get at the slice data
    """
    slc = subscript_to_slice(node)

    if isinstance(slc, ast.Tuple):
        elts: Any = []
        for v in slc.elts:
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
    if isinstance(slc, ast.UnaryOp) and isinstance(slc.op, ast.USub):
        negate = True
        slc = slc.operand

    if isinstance(slc, ast.Name):
        return slc

    if not isinstance(slc, (ast.Constant, ast.Str, ast.Num)):
        return None

    if isinstance(slc, ast.Constant):
        slc = slc.value
    elif isinstance(slc, ast.Num):  # pragma: no cover
        slc = slc.n  # type: ignore
        if not isinstance(slc, int):
            return None
    elif isinstance(slc, ast.Str):  # pragma: no cover
        slc = slc.s  # type: ignore
    else:
        return None

    if isinstance(slc, int) and negate:
        slc = -slc  # type: ignore
    return slc  # type: ignore


class Atom(CommonEqualityMixin):
    def __init__(
        self,
        value: SupportedIndexType,
        is_callpoint: bool = False,
        is_subscript: bool = False,
        is_reactive: bool = False,
        is_cascading_reactive: bool = False,
        is_blocking: bool = False,
    ) -> None:
        self.value = value
        self.is_callpoint = is_callpoint
        self.is_subscript = is_subscript
        self.is_reactive = is_reactive
        self.is_cascading_reactive = is_cascading_reactive
        self.is_blocking = is_blocking

    def nonreactive(self) -> "Atom":
        return self.__class__(
            self.value,
            is_callpoint=self.is_callpoint,
            is_subscript=self.is_subscript,
            is_reactive=False,
            is_cascading_reactive=False,
            is_blocking=self.is_blocking,
        )

    def reactive(self) -> "Atom":
        return self.__class__(
            self.value,
            is_callpoint=self.is_callpoint,
            is_subscript=self.is_subscript,
            is_reactive=True,
            is_cascading_reactive=self.is_cascading_reactive,
            is_blocking=False,
        )

    def cascading_reactive(self) -> "Atom":
        return self.__class__(
            self.value,
            is_callpoint=self.is_callpoint,
            is_subscript=self.is_subscript,
            is_reactive=self.is_reactive,
            is_cascading_reactive=True,
            is_blocking=False,
        )

    def blocking(self) -> "Atom":
        return self.__class__(
            self.value,
            is_callpoint=self.is_callpoint,
            is_subscript=self.is_subscript,
            is_reactive=False,
            is_cascading_reactive=False,
            is_blocking=True,
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.value,
                self.is_callpoint,
                self.is_subscript,
                self.is_reactive,
                self.is_cascading_reactive,
                self.is_blocking,
            )
        )

    def __repr__(self) -> str:
        return repr(str(self))

    def __str__(self) -> str:
        return str(self.value) + ("(...)" if self.is_callpoint else "")


class SymbolRefVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbol_chain: List[Atom] = []

    def __call__(
        self,
        node: Union[
            ast.Attribute,
            ast.Subscript,
            ast.Call,
            ast.Name,
            ast.ClassDef,
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.Import,
            ast.ImportFrom,
        ],
    ) -> "SymbolRef":
        try:
            self.visit(node)
        except ValueError:
            self.symbol_chain.clear()
        self.symbol_chain.reverse()
        ret = SymbolRef(self.symbol_chain)
        self.symbol_chain = []
        return ret

    def _append_atom(
        self,
        node: Union[
            ast.Name,
            ast.Attribute,
            ast.ClassDef,
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.Import,
            ast.ImportFrom,
        ],
        val: str,
        **kwargs,
    ) -> None:
        self.symbol_chain.append(
            Atom(
                val,
                is_reactive=id(node) in tracer().reactive_node_ids,
                is_cascading_reactive=id(node) in tracer().cascading_reactive_node_ids,
                is_blocking=id(node) in tracer().blocking_node_ids,
                **kwargs,
            )
        )

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            self._append_atom(node.func, node.func.attr, is_callpoint=True)
            self.visit(node.func.value)
        elif isinstance(node.func, ast.Subscript):
            if isinstance(node.func.slice, ast.Constant) or (
                isinstance(node.func.slice, ast.Index)
                and isinstance(node.func.slice.value, (ast.Str, ast.Num))  # type: ignore
            ):
                sliceval = resolve_slice_to_constant(node.func)
                self.symbol_chain.append(
                    Atom(str(sliceval), is_callpoint=True, is_subscript=True)
                )
            self.visit(node.func.value)
        elif isinstance(node.func, ast.Name):
            self.visit(node.func)
            self.symbol_chain[-1].is_callpoint = True
        elif isinstance(node.func, ast.Call):
            # TODO: handle this case too, e.g. f.g()().h
            pass
        elif isinstance(node.func, ast.Lambda):
            # TODO: handle this case too, e.g. (lambda: [1, 2])()
            pass
        else:
            # probably a user error that happens to not be a syntax error;
            # probably will fail at runtime
            # logger.error("invalid type for node.func %s" % ast.dump(node.func))
            pass

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._append_atom(node, node.attr)
        self.visit(node.value)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        resolved = resolve_slice_to_constant(node)
        if resolved is not None:
            if isinstance(resolved, ast.Name):
                # FIXME: hack to make the static checker stop here
                # In the future, it should try to attempt to resolve
                # the value of the ast.Name node
                pass
            else:
                self.symbol_chain.append(Atom(resolved, is_subscript=True))
        self.visit(node.value)

    def visit_Name(self, node: ast.Name) -> None:
        self._append_atom(node, node.id)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._append_atom(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._append_atom(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._append_atom(node, node.name)

    def visit_Import(self, node: ast.Import):
        for name in node.names:
            self._append_atom(node, name.asname or name.name)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        for name in node.names:
            self._append_atom(node, name.asname or name.name)

    def visit_Constant(self, node):
        raise ValueError("ref cannot contain literals in chain")

    def visit_Dict(self, node: ast.Dict):
        raise ValueError("ref cannot contain literals in chain")

    def visit_List(self, node: ast.List):
        raise ValueError("ref cannot contain literals in chain")

    def visit_JoinedStr(self, node: ast.JoinedStr):
        raise ValueError("ref cannot contain literals in chain")

    def visit_Num(self, node: ast.Num):
        raise ValueError("ref cannot contain literals in chain")

    def visit_Set(self, node: ast.Set):
        raise ValueError("ref cannot contain literals in chain")

    def visit_Str(self, node: ast.Str):
        raise ValueError("ref cannot contain literals in chain")

    def visit_Tuple(self, node: ast.Tuple):
        raise ValueError("ref cannot contain literals in chain")

    def generic_visit(self, node) -> None:
        # raise ValueError('we should never get here: %s' % node)
        # give up
        return


class SymbolRef(CommonEqualityMixin):
    _cached_symbol_ref_visitor = SymbolRefVisitor()

    def __init__(self, symbols: Union[ast.AST, Atom, Sequence[Atom]]) -> None:
        # FIXME: each symbol should distinguish between attribute and subscript
        # FIXME: bumped in priority 2021/09/07
        if isinstance(
            symbols,
            (
                ast.Name,
                ast.Attribute,
                ast.Subscript,
                ast.Call,
                ast.ClassDef,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.Import,
                ast.ImportFrom,
            ),
        ):
            symbols = self._cached_symbol_ref_visitor(symbols).chain
        elif isinstance(symbols, ast.AST):  # pragma: no cover
            raise TypeError("unexpected type for %s" % symbols)
        elif isinstance(symbols, Atom):
            symbols = [symbols]
        self.chain: Tuple[Atom, ...] = tuple(symbols)

    @classmethod
    def from_string(cls, symbol_str: str) -> "SymbolRef":
        return cls(ast.parse(symbol_str, mode="eval").body)

    @classmethod
    def resolve(cls, symbol_str: str) -> Optional["DataSymbol"]:
        ref = cls.from_string(symbol_str)
        for resolved in ref.gen_resolved_symbols(
            flow().global_scope, only_yield_final_symbol=True, yield_in_reverse=False
        ):
            return resolved.dsym
        return None

    def __hash__(self) -> int:
        return hash(self.chain)

    def __repr__(self) -> str:
        return repr(self.chain)

    def __str__(self) -> str:
        return repr(self)

    def nonreactive(self) -> "SymbolRef":
        return self.__class__([atom.nonreactive() for atom in self.chain])

    def gen_resolved_symbols(
        self,
        scope: "Scope",
        only_yield_final_symbol: bool,
        yield_all_intermediate_symbols: bool = False,
        inherit_reactivity: bool = True,
        yield_in_reverse: bool = False,
    ) -> Generator[ResolvedDataSymbol, None, None]:
        assert not (only_yield_final_symbol and yield_all_intermediate_symbols)
        assert not (yield_in_reverse and not yield_all_intermediate_symbols)
        dsym, atom, next_atom = None, None, None
        reactive_seen = False
        cascading_reactive_seen = False
        blocking_seen = False
        if yield_in_reverse:
            gen: Iterable[Tuple["DataSymbol", Atom, Atom]] = [
                (resolved.dsym, resolved.atom, resolved.next_atom)
                for resolved in self.gen_resolved_symbols(
                    scope,
                    only_yield_final_symbol=only_yield_final_symbol,
                    yield_all_intermediate_symbols=True,
                    inherit_reactivity=False,
                    yield_in_reverse=False,
                )
            ]
            cast(list, gen).reverse()
        else:
            gen = scope.gen_data_symbols_for_attrsub_chain(self)
        for dsym, atom, next_atom in gen:
            reactive_seen = reactive_seen or atom.is_reactive
            cascading_reactive_seen = (
                cascading_reactive_seen or atom.is_cascading_reactive
            )
            yield_all_intermediate_symbols = (
                yield_all_intermediate_symbols or reactive_seen
            )
            if inherit_reactivity:
                if reactive_seen and not blocking_seen and not atom.is_reactive:
                    atom = atom.reactive()
                if (
                    cascading_reactive_seen
                    and not blocking_seen
                    and not atom.is_cascading_reactive
                ):
                    atom = atom.cascading_reactive()
                if blocking_seen and not atom.is_blocking:
                    atom = atom.blocking()
            if yield_all_intermediate_symbols:
                # TODO: only use this branch one staleness checker can be smarter about liveness timestamps.
                #  Right now, yielding the intermediate elts of the chain will yield false positives in the
                #  event of namespace stale children.
                yield ResolvedDataSymbol(dsym, atom, next_atom)
        if not yield_all_intermediate_symbols and dsym is not None:
            if next_atom is None or not only_yield_final_symbol:
                yield ResolvedDataSymbol(dsym, atom, next_atom)


class LiveSymbolRef(CommonEqualityMixin):
    def __init__(
        self, ref: SymbolRef, timestamp: int, is_lhs_ref: bool = False
    ) -> None:
        self.ref = ref
        self.timestamp = timestamp
        self.is_lhs_ref = is_lhs_ref

    @classmethod
    def from_string(cls, symbol_str: str, **kwargs) -> "LiveSymbolRef":
        kwargs["timestamp"] = kwargs.get("timestamp", 0)
        return cls(SymbolRef.from_string(symbol_str), **kwargs)

    @staticmethod
    def resolve(symbol_str: str) -> Optional["DataSymbol"]:
        return SymbolRef.resolve(symbol_str)

    def __hash__(self) -> int:
        return hash((self.ref, self.timestamp, self.is_lhs_ref))

    def __str__(self) -> str:
        return (
            f"<live:{self.ref}@{self.timestamp}{' (lhs)' if self.is_lhs_ref else ''}>"
        )

    def __repr__(self) -> str:
        return str(self)

    def gen_resolved_symbols(
        self,
        scope: "Scope",
        only_yield_final_symbol: bool,
        yield_all_intermediate_symbols: bool = False,
        cell_ctr: int = -1,
    ) -> Generator[ResolvedDataSymbol, None, None]:
        blocking_seen = False
        for resolved_sym in self.ref.gen_resolved_symbols(
            scope,
            only_yield_final_symbol,
            yield_all_intermediate_symbols=yield_all_intermediate_symbols,
        ):
            resolved_sym.is_lhs_ref = self.is_lhs_ref
            resolved_sym.liveness_timestamp = Timestamp(cell_ctr, self.timestamp)
            blocking_seen = blocking_seen or resolved_sym.is_blocking
            if blocking_seen and not resolved_sym.is_blocking:
                resolved_sym.atom = resolved_sym.atom.blocking()
            yield resolved_sym
