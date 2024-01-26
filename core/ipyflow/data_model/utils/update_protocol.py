# -*- coding: utf-8 -*-
import logging
import sys
from typing import TYPE_CHECKING, Generator, Iterable, Set, cast

from ipyflow.data_model import DUPED_ATTRSUB_CLASSES
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.singletons import flow, tracer

if TYPE_CHECKING:
    # avoid circular imports
    from ipyflow.data_model.symbol import Symbol

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


class UpdateProtocol:
    def __init__(self, updated_sym: "Symbol") -> None:
        self.updated_sym = updated_sym
        self.seen: Set["Symbol"] = set()

    def __call__(
        self,
        new_deps: Set["Symbol"],
        mutated: bool,
        propagate_to_namespace_descendents: bool,
        refresh: bool,
    ) -> None:
        # in most cases, mutated implies that we should propagate to namespace descendents, since we
        # do not know how the mutation affects the namespace members. The exception is for specific
        # known events such as 'list.append()' or 'list.extend()' since we know these do not update
        # the namespace members.
        logger.warning(
            "updated sym %s (containing scope %s) with children %s",
            self.updated_sym,
            self.updated_sym.containing_scope,
            self.updated_sym.children,
        )
        directly_updated_symbols = (
            flow().aliases[self.updated_sym.obj_id] if mutated else {self.updated_sym}
        )
        directly_updated_symbols |= self._maybe_get_duped_attrsub_updated_syms()
        self._collect_updated_symbols_and_refresh_namespaces(
            directly_updated_symbols, propagate_to_namespace_descendents
        )
        logger.warning(
            "for symbol %s: mutated=%s; updated_symbols=%s",
            self.updated_sym,
            mutated,
            directly_updated_symbols,
        )
        updated_symbols_with_ancestors = set(self.seen)
        logger.warning(
            "all updated symbols for symbol %s: %s",
            self.updated_sym,
            updated_symbols_with_ancestors,
        )
        tracer().this_stmt_updated_symbols |= self.seen
        if refresh:
            for updated_sym in directly_updated_symbols:
                if not updated_sym.is_waiting and updated_sym is not self.updated_sym:
                    updated_sym.refresh()
        self.seen |= new_deps  # don't propagate to stuff on RHS
        for sym in updated_symbols_with_ancestors:
            self._propagate_waiting_to_deps(sym, skip_seen_check=True)

    def _maybe_get_duped_attrsub_updated_syms(self) -> Set["Symbol"]:
        for modname, classname in DUPED_ATTRSUB_CLASSES:
            module = sys.modules.get(modname, None)
            if modname is None:
                continue
            clazz = getattr(module, classname, None)
            if clazz is None:
                continue

            ns = self.updated_sym.containing_namespace
            if ns is None or ns.obj is None or not isinstance(ns.obj, clazz):
                continue

            name = self.updated_sym.name
            return cast(
                Set["Symbol"],
                {
                    ns.lookup_symbol_by_name_this_indentation(name, is_subscript=is_sub)
                    for is_sub in (True, False)
                }
                - {None},
            )
        return set()

    def _collect_updated_symbols_and_refresh_namespaces(
        self,
        updated_symbols: Iterable["Symbol"],
        refresh_descendent_namespaces: bool,
    ) -> None:
        # TODO: can this method be unified with symbol.refresh() with bump_version=False?
        logger.warning(
            "collecting updated symbols and namespaces for %s", updated_symbols
        )
        for sym in updated_symbols:
            if sym.is_import or sym in self.seen:
                continue
            # TODO: why was this present before?
            # sym.updated_timestamps.add(Timestamp.current())
            sym.required_timestamp = Timestamp.uninitialized()
            self.seen.add(sym)
            for cell in sym.cells_where_deep_live:
                cell.add_used_cell_counter(sym, flow().cell_counter())
            containing_ns = None if sym.is_module else sym.containing_namespace
            if containing_ns is not None:
                logger.warning(
                    "containing scope for %s: %s; ids %s, %s",
                    sym,
                    containing_ns,
                    sym.obj_id,
                    containing_ns.obj_id,
                )
                containing_ns.namespace_waiting_symbols.discard(sym)
                containing_ns.max_descendent_timestamp = Timestamp.current()
                self._collect_updated_symbols_and_refresh_namespaces(
                    flow().aliases.get(containing_ns.obj_id, set()),
                    refresh_descendent_namespaces,
                )
            if refresh_descendent_namespaces:
                sym_ns = sym.namespace
                if sym_ns is not None:
                    self._collect_updated_symbols_and_refresh_namespaces(
                        sym_ns.all_symbols_this_indentation(),
                        refresh_descendent_namespaces,
                    )

    def _propagate_waiting_to_namespace_parents(
        self, sym: "Symbol", skip_seen_check: bool = False
    ) -> None:
        if not skip_seen_check and sym in self.seen:
            return
        self.seen.add(sym)
        containing_ns = sym.containing_namespace
        if containing_ns is None or containing_ns.is_module:
            return
        logger.warning("add %s to namespace waiting symbols of %s", sym, containing_ns)
        containing_ns.namespace_waiting_symbols.add(sym)
        for containing_alias in flow().aliases.get(containing_ns.obj_id, []):
            self._propagate_waiting_to_namespace_parents(containing_alias)

        for containing_alias in flow().aliases.get(containing_ns.obj_id, []):
            # do this in 2 separate loops to make sure all containing_alias are added to 'seen'
            # works around the issue when one alias depends on another
            for child in self._non_class_to_instance_children(containing_alias):
                logger.warning(
                    "propagate from namespace parent of %s to child %s", sym, child
                )
                self._propagate_waiting_to_deps(child)

    def _non_class_to_instance_children(
        self, sym: "Symbol"
    ) -> Generator["Symbol", None, None]:
        if self.updated_sym is sym:
            yield from sym.children
            return
        for child in sym.children:
            # Next, complicated check to avoid propagating along a class -> instance edge.
            # The only time this is OK is when we changed the class, which will not be the case here.
            child_namespace = child.namespace
            if child_namespace is not None and child_namespace.cloned_from is not None:
                if child_namespace.cloned_from.obj_id == sym.obj_id:
                    continue
            yield child

    def _propagate_waiting_to_namespace_children(
        self, sym: "Symbol", skip_seen_check: bool = False
    ) -> None:
        if not skip_seen_check and sym in self.seen:
            return
        self.seen.add(sym)
        self_ns = flow().namespaces.get(sym.obj_id)
        if self_ns is None:
            return
        for ns_child in self_ns.all_symbols_this_indentation(exclude_class=True):
            logger.warning("propagate from %s to namespace child %s", sym, ns_child)
            self._propagate_waiting_to_deps(ns_child)

    def _propagate_waiting_to_deps(
        self, sym: "Symbol", skip_seen_check: bool = False
    ) -> None:
        if not skip_seen_check and sym in self.seen:
            return
        self.seen.add(sym)
        if (
            sym not in flow().updated_symbols
            and sym not in tracer().this_stmt_updated_symbols
        ):
            if sym.should_mark_waiting(self.updated_sym):
                sym.fresher_ancestors.add(self.updated_sym)
                sym.fresher_ancestor_timestamps.add(self.updated_sym.timestamp)
                sym.required_timestamp = Timestamp.current()
                self._propagate_waiting_to_namespace_parents(sym, skip_seen_check=True)
                self._propagate_waiting_to_namespace_children(sym, skip_seen_check=True)
        for child in self._non_class_to_instance_children(sym):
            logger.warning("propagate %s %s to %s", sym, sym.obj_id, child)
            self._propagate_waiting_to_deps(child)
