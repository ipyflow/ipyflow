# -*- coding: utf-8 -*-
import logging
from typing import cast, TYPE_CHECKING

from nbsafety.ipython_utils import cell_counter

if TYPE_CHECKING:
    from typing import Set
    from nbsafety.data_model.data_symbol import DataSymbol
    from nbsafety.data_model.scope import NamespaceScope
    from nbsafety.safety import NotebookSafety

logger = logging.getLogger(__name__)


class UpdateProtocol(object):
    def __init__(self, safety: 'NotebookSafety', updated_sym: 'DataSymbol', mutated: bool):
        self.safety = safety
        self.updated_sym = updated_sym
        self.mutated = mutated
        self.updated_symbols: Set[DataSymbol] = set()

    def __call__(self, propagate=True):
        self.updated_sym.defined_cell_num = cell_counter()
        if propagate:
            self._collect_updated_symbols(self.updated_sym)
        self.safety.updated_symbols = self.updated_symbols

    def _collect_updated_symbols(self, dsym: 'DataSymbol'):
        if dsym in self.updated_symbols:
            return
        self.updated_symbols.add(dsym)
        for dsym_alias in self.safety.aliases[dsym.obj_id]:
            containing_scope: 'NamespaceScope' = cast('NamespaceScope', dsym_alias.containing_scope)
            if not containing_scope.is_namespace_scope:
                continue
            # self.safety.updated_scopes.add(containing_scope)
            containing_scope.max_defined_timestamp = cell_counter()
            containing_namespace_obj_id = containing_scope.obj_id
            for alias in self.safety.aliases[containing_namespace_obj_id]:
                alias.namespace_stale_symbols.discard(dsym)
                self._collect_updated_symbols(alias)
