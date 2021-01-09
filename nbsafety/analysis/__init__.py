# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

from .symbol_edges import get_assignment_lval_and_rval_symbol_refs, get_symbol_edges
from .attr_symbols import AttrSubSymbolChain, CallPoint, get_attrsub_symbol_chain
from .live_refs import compute_live_dead_symbol_refs
from .utils import compute_call_chain_live_symbols, get_symbols_for_references, stmt_contains_lval

if TYPE_CHECKING:
    from .attr_symbols import AttrSubChainType