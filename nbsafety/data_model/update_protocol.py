# -*- coding: future_annotations -*-
import logging
from typing import TYPE_CHECKING

from nbsafety.singletons import nbs

if TYPE_CHECKING:
    from typing import Iterable, Set

    # avoid circular imports
    from nbsafety.data_model.data_symbol import DataSymbol

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


class UpdateProtocol:
    def __init__(self, updated_sym: DataSymbol) -> None:
        self.updated_sym = updated_sym
        self.seen: Set[DataSymbol] = set()

    def __call__(self, new_deps: Set[DataSymbol], mutated: bool, propagate_to_namespace_descendents: bool) -> None:
        # in most cases, mutated implies that we should propagate to namespace descendents, since we
        # do not know how the mutation affects the namespace members. The exception is for specific
        # known events such as 'list.append()' or 'list.extend()' since we know these do not update
        # the namespace members.
        logger.warning(
            "updated sym %s (containing scope %s) with children %s",
            self.updated_sym,
            self.updated_sym.containing_scope,
            self.updated_sym.children_by_cell_position.values(),
        )
        directly_updated_symbols = nbs().aliases[self.updated_sym.obj_id] if mutated else {self.updated_sym}
        self._collect_updated_symbols_and_refresh_namespaces(
            directly_updated_symbols, propagate_to_namespace_descendents
        )
        logger.warning(
            'for symbol %s: mutated=%s; updated_symbols=%s', self.updated_sym, mutated, directly_updated_symbols
        )
        updated_symbols_with_ancestors = set(self.seen)
        logger.warning('all updated symbols for symbol %s: %s', self.updated_sym, updated_symbols_with_ancestors)
        nbs().updated_symbols |= self.seen
        for updated_sym in directly_updated_symbols:
            if not updated_sym.is_stale and updated_sym is not self.updated_sym:
                updated_sym.refresh()
        self.seen |= new_deps  # don't propagate to stuff on RHS
        for dsym in updated_symbols_with_ancestors:
            self._propagate_staleness_to_deps(dsym, skip_seen_check=True)

    def _collect_updated_symbols_and_refresh_namespaces(
        self, updated_symbols: Iterable[DataSymbol], refresh_descendent_namespaces: bool
    ) -> None:
        logger.warning('collecting updated symbols and namespaces for %s', updated_symbols)
        for dsym in updated_symbols:
            if dsym.is_import or dsym in self.seen:
                continue
            dsym.updated_timestamps.add(nbs().cell_counter())
            self.seen.add(dsym)
            containing_ns = dsym.containing_namespace
            if containing_ns is not None:
                logger.warning('containing scope for %s: %s; ids %s, %s', dsym, containing_ns, dsym.obj_id, containing_ns.obj_id)
                containing_ns.namespace_stale_symbols.discard(dsym)
                containing_ns.max_descendent_timestamp = nbs().cell_counter()
                self._collect_updated_symbols_and_refresh_namespaces(
                    nbs().aliases[containing_ns.obj_id], refresh_descendent_namespaces
                )
            if refresh_descendent_namespaces:
                dsym_ns = dsym.namespace
                if dsym_ns is not None:
                    self._collect_updated_symbols_and_refresh_namespaces(
                        dsym_ns.all_data_symbols_this_indentation(), refresh_descendent_namespaces
                    )

    def _propagate_staleness_to_namespace_parents(self, dsym: DataSymbol, skip_seen_check=False):
        if not skip_seen_check and dsym in self.seen:
            return
        self.seen.add(dsym)
        containing_ns = dsym.containing_namespace
        if containing_ns is None:
            return
        logger.warning("add %s to namespace stale symbols of %s", dsym, containing_ns)
        containing_ns.namespace_stale_symbols.add(dsym)
        for containing_alias in nbs().aliases[containing_ns.obj_id]:
            self._propagate_staleness_to_namespace_parents(containing_alias)

        for containing_alias in nbs().aliases[containing_ns.obj_id]:
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
        self_ns = nbs().namespaces.get(dsym.obj_id, None)
        if self_ns is None:
            return
        for ns_child in self_ns.all_data_symbols_this_indentation(exclude_class=True):
            logger.warning('propagate from %s to namespace child %s', dsym, ns_child)
            self._propagate_staleness_to_deps(ns_child)

    def _propagate_staleness_to_deps(self, dsym: DataSymbol, skip_seen_check=False):
        if not skip_seen_check and dsym in self.seen:
            return
        self.seen.add(dsym)
        if dsym not in nbs().updated_symbols:
            if dsym.should_mark_stale(self.updated_sym):
                dsym.fresher_ancestors.add(self.updated_sym)
                dsym.required_timestamp = nbs().cell_counter()
                self._propagate_staleness_to_namespace_parents(dsym, skip_seen_check=True)
                self._propagate_staleness_to_namespace_children(dsym, skip_seen_check=True)
        for child in self._non_class_to_instance_children(dsym):
            logger.warning('propagate %s %s to %s', dsym, dsym.obj_id, child)
            self._propagate_staleness_to_deps(child)
