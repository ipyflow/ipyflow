# -*- coding: utf-8 -*-
import ast
import logging
import sys
from typing import cast, TYPE_CHECKING

from nbsafety.analysis.attr_symbols import AttrSubSymbolChain
from nbsafety.analysis.live_refs import compute_live_dead_symbol_refs

if TYPE_CHECKING:
    from nbsafety.data_model.data_symbol import DataSymbol
    from nbsafety.data_model.scope import Scope
    from nbsafety.types import SymbolRef
    from typing import Set, Tuple

logger = logging.getLogger(__name__)


def get_symbols_for_references(
        symbol_refs: 'Set[SymbolRef]',
        scope: 'Scope',
        only_add_successful_resolutions: bool = False,
) -> 'Tuple[Set[DataSymbol], Set[DataSymbol]]':
    symbols = set()
    called_symbols = set()
    for symbol_ref in symbol_refs:
        success = True
        if isinstance(symbol_ref, str):
            dsym = scope.lookup_data_symbol_by_name(symbol_ref)
            called_dsym = None
        elif isinstance(symbol_ref, AttrSubSymbolChain):
            dsym, called_dsym, success = scope.get_most_specific_data_symbol_for_attrsub_chain(symbol_ref)
        else:
            logger.warning('invalid type for ref %s', symbol_ref)
            continue
        if dsym is not None:
            if success or not only_add_successful_resolutions:
                symbols.add(dsym)
        if called_dsym is not None:
            called_symbols.add(called_dsym)
    return symbols, called_symbols


def compute_call_chain_live_symbols(live: 'Set[DataSymbol]'):
    seen = set()
    worklist = list(live)
    while len(worklist) > 0:
        called_dsym = worklist.pop()
        if called_dsym in seen:
            continue
        seen.add(called_dsym)
        # TODO: handle callable classes
        if not called_dsym.is_function:
            continue
        live_refs, _ = compute_live_dead_symbol_refs(
            cast(ast.FunctionDef, called_dsym.stmt_node).body, called_dsym.get_call_args()
        )
        live_symbols, called_symbols = get_symbols_for_references(live_refs, called_dsym.call_scope)
        worklist.extend(called_symbols)
        live_symbols = set(sym for sym in live_symbols if sym.is_globally_accessible)
        called_symbols = set(sym for sym in called_symbols if sym.is_globally_accessible)
        live |= live_symbols.union(called_symbols)
    return live


class ContainsNamedExprVisitor(ast.NodeVisitor):
    def __init__(self):
        self.contains_named_expr = False

    def __call__(self, node: 'ast.stmt') -> bool:
        if sys.version_info.minor < 8:
            return False
        self.visit(node)
        return self.contains_named_expr

    def visit_NamedExpr(self, node):
        self.contains_named_expr = True

    def generic_visit(self, node: 'ast.AST'):
        if self.contains_named_expr:
            return
        super().generic_visit(node)


def stmt_contains_lval(node: 'ast.stmt'):
    # TODO: expand to method calls, etc.
    simple_contains_lval = isinstance(node, (
        ast.Assign,
        ast.AnnAssign,
        ast.AugAssign,
        ast.ClassDef,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.For,
        ast.Import,
        ast.ImportFrom,
        ast.With,
    ))
    return simple_contains_lval or ContainsNamedExprVisitor()(node)
