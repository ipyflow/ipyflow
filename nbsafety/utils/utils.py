# -*- coding: utf-8 -*-
import ast
import logging
from typing import cast, TYPE_CHECKING

from ..analysis import AttrSubSymbolChain, compute_live_dead_symbol_refs

if TYPE_CHECKING:
    from ..data_symbol import DataSymbol
    from ..scope import Scope
    from ..types import SymbolRef
    from typing import Any, Set, Tuple, Union

logger = logging.getLogger(__name__)


def retrieve_namespace_attr_or_sub(obj: 'Any', attr_or_sub: 'Union[str, int]', is_subscript: bool):
    try:
        if is_subscript:
            # TODO: more complete list of things that are checkable
            #  or could cause side effects upon subscripting
            if isinstance(obj, dict) and attr_or_sub not in obj:
                raise KeyError()
            else:
                return obj[attr_or_sub]
        else:
            assert isinstance(attr_or_sub, str)
            if not hasattr(obj, attr_or_sub):
                raise AttributeError()
            else:
                return getattr(obj, attr_or_sub)
    except (KeyError, IndexError, AttributeError):
        raise
    # except AssertionError as e:
    #     print(obj, attr_or_sub, is_subscript)
    #     raise e
    except Exception as e:
        logger.warning('unexpected exception: %s', e)
        logger.warning('object: %s', obj)
        logger.warning('attr / subscript: %s', attr_or_sub)
        raise e


def get_symbols_for_references(
        symbol_refs: 'Set[SymbolRef]', scope: 'Scope'
) -> 'Tuple[Set[DataSymbol], Set[DataSymbol]]':
    symbols = set()
    called_symbols = set()
    for symbol_ref in symbol_refs:
        if isinstance(symbol_ref, str):
            dsym = scope.lookup_data_symbol_by_name(symbol_ref)
            called_dsym = None
        elif isinstance(symbol_ref, AttrSubSymbolChain):
            dsym, called_dsym = scope.get_most_specific_data_symbol_for_attrsub_chain(symbol_ref)
        else:
            logger.warning('invalid type for ref %s', symbol_ref)
            continue
        if dsym is not None:
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
