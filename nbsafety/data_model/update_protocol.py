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
        self.seen: Set[DataSymbol] = set()

    def __call__(self, propagate=True):
        if propagate:
            self._collect_updated_symbols(self.updated_sym)
        self.safety.updated_symbols = set(self.seen)
        for dsym in self.safety.updated_symbols:
            self._propagate_update_to_deps(dsym, updated=True)
        # important! don't bump defined_cell_num until the very end!
        #  need to wait until here because, by default,
        #  we don't want to propagate to symbols defined in the same cell
        self.updated_sym.defined_cell_num = cell_counter()

    def _collect_updated_symbols(self, dsym: 'DataSymbol'):
        if dsym in self.seen:
            return
        self.seen.add(dsym)
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

    def _propagate_update_to_deps(self, dsym: 'DataSymbol', updated=False):
        if not updated:
            if dsym in self.seen:
                return
            self.seen.add(dsym)
            if dsym.should_mark_stale(self.updated_sym):
                dsym.fresher_ancestors.add(self.updated_sym)
                dsym.required_cell_num = cell_counter()
        for child in dsym.children:
            self._propagate_update_to_deps(child)
