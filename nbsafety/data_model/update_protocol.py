# -*- coding: future_annotations -*-
import logging
from typing import cast, TYPE_CHECKING

from nbsafety.singletons import nbs

if TYPE_CHECKING:
    from typing import Set

    # avoid circular imports
    from nbsafety.data_model.data_symbol import DataSymbol
    from nbsafety.data_model.scope import NamespaceScope

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


class UpdateProtocol:
    def __init__(
        self,
        updated_sym: DataSymbol,
        new_deps: Set[DataSymbol],
        mutated: bool,
        deleted: bool,
    ):
        self.updated_sym = updated_sym
        self.new_deps = new_deps
        self.mutated = mutated
        self.deleted = deleted
        self.seen: Set[DataSymbol] = set()

    def __call__(self, propagate=True):
        logger.warning("updated sym %s (containing scope %s) with children %s", self.updated_sym,
                       self.updated_sym.containing_scope,
                       self.updated_sym.children_by_cell_position.values())
        namespace_refresh = None
        if propagate:
            if self.mutated or self.deleted or self.updated_sym.obj_id != self.updated_sym.cached_obj_id:
                self._collect_updated_symbols(self.updated_sym, skip_aliases=not self.mutated)
            if self.updated_sym.cached_obj_id is not None:
                # TODO: also condition on non simple assign
                namespace = nbs().namespaces.get(self.updated_sym.obj_id, None)
                if namespace is not None:
                    # TODO: go deeper?
                    namespace_refresh = set(namespace.all_data_symbols_this_indentation())
        updated_symbols = set(self.seen)
        logger.warning('for symbol %s: mutated=%s; updated_symbols=%s', self.updated_sym, self.mutated, updated_symbols)
        nbs().updated_symbols |= updated_symbols
        self.seen |= self.new_deps  # don't propagate to stuff on RHS
        for dsym in updated_symbols:
            self._propagate_staleness_to_deps(dsym, skip_seen_check=True)
        # important! don't bump defined_cell_num until the very end!
        for updated_sym in updated_symbols:
            if not updated_sym.is_stale:
                updated_sym.refresh()
        self.updated_sym.refresh()
        if namespace_refresh is not None:
            for updated_sym in namespace_refresh:
                for updated_sym_alias in nbs().aliases.get(updated_sym.obj_id, []):
                    updated_sym_alias.refresh()

    def _collect_updated_symbols(self, dsym: DataSymbol, skip_aliases=False):
        if dsym.is_import:
            return
        if skip_aliases:
            aliases_to_consider = {dsym}
        else:
            aliases_to_consider = nbs().aliases[dsym.obj_id]
        logger.warning('collecting updates symbols for %s', aliases_to_consider)
        for dsym_alias in aliases_to_consider:
            if dsym_alias.is_import or dsym_alias in self.seen:
                continue
            self.seen.add(dsym_alias)
            containing_scope: NamespaceScope = cast('NamespaceScope', dsym_alias.containing_scope)
            if not containing_scope.is_namespace_scope:
                continue
            logger.warning('containing scope for %s: %s; ids %s, %s', dsym_alias, containing_scope, dsym_alias.obj_id, containing_scope.obj_id)
            # TODO: figure out what this is for again
            # nbs().updated_scopes.add(containing_scope)
            containing_scope.max_defined_timestamp = nbs().cell_counter()
            containing_namespace_obj_id = containing_scope.obj_id
            for alias in nbs().aliases[containing_namespace_obj_id]:
                alias.namespace_stale_symbols.discard(dsym)
                # print('discard stale', dsym, 'from', alias, 'namespace, has fresher ancestors:', alias.fresher_ancestors)
                self._collect_updated_symbols(alias)

    def _propagate_staleness_to_namespace_parents(self, dsym: DataSymbol, skip_seen_check=False):
        if not skip_seen_check and dsym in self.seen:
            return
        self.seen.add(dsym)
        containing_scope: NamespaceScope = cast('NamespaceScope', dsym.containing_scope)
        if containing_scope is None or not containing_scope.is_namespace_scope:
            return
        for containing_alias in nbs().aliases[containing_scope.obj_id]:
            containing_alias.namespace_stale_symbols.add(dsym)
            self._propagate_staleness_to_namespace_parents(containing_alias)

        for containing_alias in nbs().aliases[containing_scope.obj_id]:
            # do this in 2 separate loops to make sure all containing_alias are added to 'seen'
            # works around the issue when one alias depends on another
            for child in self._non_class_to_instance_children(containing_alias):
                logger.warning('propagate from namespace parent of %s to child %s', dsym, child)
                self._propagate_staleness_to_deps(child)

    def _non_class_to_instance_children(self, dsym):
        if self.updated_sym is dsym:
            for dep_introduced_pos, dsym_children in dsym.children_by_cell_position.items():
                if not nbs().settings.backwards_cell_staleness_propagation and dep_introduced_pos <= nbs().active_cell_position_idx:
                    continue
                yield from dsym_children
            return
        for dep_introduced_pos, dsym_children in dsym.children_by_cell_position.items():
            if not nbs().settings.backwards_cell_staleness_propagation and dep_introduced_pos <= nbs().active_cell_position_idx:
                continue
            for child in dsym_children:
                # Next, complicated check to avoid propagating along a class -> instance edge.
                # The only time this is OK is when we changed the class, which will not be the case here.
                child_namespace = child.namespace
                if child_namespace is not None and child_namespace.cloned_from is not None:
                    if child_namespace.cloned_from.obj_id == dsym.obj_id:
                        continue
                yield child

    def _propagate_staleness_to_namespace_children(self, dsym: DataSymbol, skip_seen_check=False):
        if not skip_seen_check and dsym in self.seen:
            return
        self.seen.add(dsym)
        self_scope = nbs().namespaces.get(dsym.obj_id, None)
        if self_scope is None:
            return
        for ns_child in self_scope.all_data_symbols_this_indentation(exclude_class=True):
            logger.warning('propagate from %s to namespace child %s', dsym, ns_child)
            self._propagate_staleness_to_deps(ns_child)

    def _propagate_staleness_to_deps(self, dsym: DataSymbol, skip_seen_check=False):
        if not skip_seen_check and dsym in self.seen:
            return
        self.seen.add(dsym)
        if dsym not in nbs().updated_symbols:
            if dsym.should_mark_stale(self.updated_sym):
                dsym.fresher_ancestors.add(self.updated_sym)
                dsym.required_cell_num = nbs().cell_counter()
                self._propagate_staleness_to_namespace_parents(dsym, skip_seen_check=True)
                self._propagate_staleness_to_namespace_children(dsym, skip_seen_check=True)
        for child in self._non_class_to_instance_children(dsym):
            logger.warning('propagate %s %s to %s', dsym, dsym.obj_id, child)
            self._propagate_staleness_to_deps(child)
