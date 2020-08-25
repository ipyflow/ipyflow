# -*- coding: utf-8 -*-
import logging
from typing import cast, TYPE_CHECKING

from nbsafety.ipython_utils import cell_counter

if TYPE_CHECKING:
    from typing import Any, Set
    from nbsafety.data_model.data_symbol import DataSymbol
    from nbsafety.data_model.scope import NamespaceScope
    from nbsafety.safety import NotebookSafety

logger = logging.getLogger(__name__)
NOT_FOUND = object()


class LegacyUpdateProtocol(object):
    def __init__(self, safety: 'NotebookSafety', updated_dep: 'DataSymbol', mutated: bool):
        self.safety = safety
        self.updated_sym = updated_dep
        self.seen: Set[DataSymbol] = set()
        self.parent_seen: Set[DataSymbol] = set()
        self.mutated = mutated

    def __call__(self, propagate=True):
        if propagate:
            # print(self, 'update deps')
            self._propagate_update(self.updated_sym, self.updated_sym._get_obj(), refresh=True)

    def _propagate_update_to_deps(self, dsym: 'DataSymbol'):
        # print(self, 'propagate to child deps', self.children)
        if dsym.should_mark_stale(self.updated_sym):
            # if self.full_namespace_path == updated_dep.full_namespace_path:
            #     print('weird', self.full_namespace_path, self, updated_dep, self.obj_id, updated_dep.obj_id, self.cached_obj_id, updated_dep.cached_obj_id)
            dsym.fresher_ancestors.add(self.updated_sym)
            dsym.required_cell_num = cell_counter()
        for child in dsym.children:
            # print(self, 'prop to', child, self._get_children_to_skip())
            self._propagate_update(child, child._get_obj())

    def _get_children_to_skip(self, dsym: 'DataSymbol', obj_id=None):
        # TODO: this should probably DFS in order to skip all namespace ancestors
        if obj_id is None:
            obj_id = dsym.obj_id
        children_to_skip = set()
        # skip any children that themselves contain an alias of self
        # TODO: this definitely won't work unless we are aware of containing namespaces
        #  need to therefore create data symbols for list literals and things like lst `append`
        for self_alias in self.safety.aliases[obj_id]:
            containing_obj_id = getattr(self_alias.containing_scope, 'obj_id', None)
            if containing_obj_id is not None:
                children_to_skip |= self.safety.aliases[containing_obj_id]
        return children_to_skip

    def _propagate_update(self, dsym: 'DataSymbol', new_parent_obj: 'Any', refresh=False):
        # look at old obj_id and cur obj_id
        # walk down namespace hierarchy from old obj_id, and track corresponding DCs from cur obj_id
        # a few cases to consider:
        # 1. mismatched obj ids or unavailable from new hierarchy:
        #    mark old dc as mutated AND stale, and propagate to old dc children
        # 2. new / old DataSymbols have same obj ids:
        #    mark old dc as mutated, but NOT stale, and propagate to children
        #    Q: should old dc additionally be refreshed?
        #    Technically it should already be fresh, since if it's still a descendent of this namespace, we probably
        #    had to ref the namespace ancestor, which should have been caught by the checker if the descendent has
        #    some other stale ancestor. If not fresh, let's mark it so and log a warning about a potentially stale usage
        if dsym._tombstone or dsym in self.seen:
            return
        self.seen.add(dsym)
        # print('propagate update from', self)

        new_id = None if new_parent_obj is NOT_FOUND else id(new_parent_obj)

        if self.updated_sym is dsym:
            old_parent_obj = dsym._get_cached_obj()
            old_id = dsym.cached_obj_id
        else:
            old_parent_obj = dsym._get_obj()
            old_id = dsym.obj_id

        namespace = self.safety.namespaces.get(old_id, None)
        if namespace is None and refresh:  # if we are at a leaf
            self._propagate_update_to_namespace_parents(dsym, refresh=refresh)
        if namespace is not None and (old_id != new_id or self.mutated or not refresh):
            for dc in namespace.all_data_symbols_this_indentation(exclude_class=True):
                should_refresh = refresh  # or updated_dep in set(namespace.all_data_symbols_this_indentation())
                dc_in_self_namespace = False
                if new_parent_obj is NOT_FOUND:
                    self._propagate_update(dc, NOT_FOUND, refresh=should_refresh)
                else:
                    try:
                        obj = dsym._get_obj()
                        obj_attr_or_sub = self.safety.retrieve_namespace_attr_or_sub(obj, dc.name, dc.is_subscript)
                        # print(dc, obj, obj_attr_or_sub, updated_dep, seen, parent_seen, refresh, mutated, old_id, new_id)
                        self._propagate_update(dc, obj_attr_or_sub, refresh=should_refresh)
                        dc_in_self_namespace = True
                        if new_parent_obj is not old_parent_obj and new_id is not None:
                            new_namespace = self.safety.namespaces.get(new_id, None)
                            if new_namespace is None:
                                new_namespace = namespace.shallow_clone(new_parent_obj)
                                self.safety.namespaces[new_id] = new_namespace
                            # TODO: handle class data cells properly;
                            #  in fact; we still need to handle aliases of class data cells
                            if dc.name not in new_namespace.data_symbol_by_name(dc.is_subscript):
                                new_dc = dc.shallow_clone(obj_attr_or_sub, new_namespace, dc.symbol_type)
                                new_namespace.put(dc.name, new_dc)
                                self.safety.updated_symbols.add(new_dc)
                    except (KeyError, IndexError, AttributeError):
                        self._propagate_update(dc, NOT_FOUND, refresh=should_refresh)
                if dc_in_self_namespace and dc.should_mark_stale(self.updated_sym):
                    # print(self, 'add', dc, 'to namespace stale symbols due to', updated_dep, self.defined_cell_num, dc.defined_cell_num, updated_dep.defined_cell_num, dc.fresher_ancestors)
                    dsym.namespace_stale_symbols.add(dc)
                else:
                    dsym.namespace_stale_symbols.discard(dc)

        # if mutated or self.cached_obj_id != self.obj_id:
        # if (old_id != new_id or not refresh) and not mutated:
        #     self._propagate_update_to_deps(updated_dep, seen, parent_seen)
        # elif mutated:
        if old_id != new_id or not refresh or self.mutated:
            to_skip = self._get_children_to_skip(dsym)
            # self.seen |= to_skip
            old_seen = self.seen
            self.seen = self.seen | to_skip
            self._propagate_update_to_deps(dsym)
            self.seen = old_seen
            # print(self, 'propagate+skip done')

        if self.updated_sym is dsym:
            return

        if refresh:
            for alias in self.safety.aliases[old_id]:
                if alias.defined_cell_num < alias.required_cell_num < cell_counter():
                    logger.debug('possible stale usage of namespace descendent %s' % alias)
                if len(alias.namespace_stale_symbols) > 0:
                    logger.debug('unexpected stale namespace symbols for symbol %s: %s' % (alias, alias.namespace_stale_symbols))
                    alias.namespace_stale_symbols.clear()
                if old_id != new_id or self.mutated:
                    # TODO: better equality testing
                    #  Doing equality testing properly requires that we still have a reference to old object around;
                    #  we should be using weak refs, which complicates this.
                    #  Depth also makes this challenging.
                    self._propagate_update_to_deps(alias)
                # TODO: this will probably work, but will also unnecessarily propagate the refresh up to
                #  namespace parents. Need a mechanism to mark for refresh without parent propagation.
                self.safety.updated_symbols.add(alias)
        else:
            # propagating to all aliases was causing problems
            # see test `test_class_redef`
            self._propagate_update_to_deps(dsym)
            # for alias in self.safety.aliases[old_id]:
            #     # print('propagate', updated_dep, 'to', alias, 'via', self, updated_dep.defined_cell_num, alias.defined_cell_num, self.defined_cell_num)
            #     alias._propagate_update_to_deps(updated_dep, seen, parent_seen)
            if namespace is None and dsym.should_mark_stale(self.updated_sym):  # if we are at a leaf
                # print('propagate', updated_dep, 'to', self, updated_dep.defined_cell_num, self.defined_cell_num)
                self._propagate_update_to_namespace_parents(dsym, refresh=refresh)
        # print(self, 'done')

    def _propagate_update_to_namespace_parents(self, dsym: 'DataSymbol', refresh):
        if not dsym.containing_scope.is_namespace_scope or dsym in self.parent_seen:
            return
        # print('parent propagate', self)
        self.parent_seen.add(dsym)
        containing_scope = cast('NamespaceScope', dsym.containing_scope)
        containing_namespace_obj_ids_to_consider = {containing_scope.obj_id}
        # if refresh:
        #     for self_alias in self.safety.aliases[self.obj_id]:
        #         if self_alias.containing_scope.is_namespace_scope:
        #             containing_namespace_obj_ids_to_consider.add(getattr(self_alias.containing_scope, 'obj_id', None))
        #     containing_namespace_obj_ids_to_consider.discard(None)
        for containing_namespace_obj_id in containing_namespace_obj_ids_to_consider:
            if containing_namespace_obj_id in self.parent_seen:
                continue
            self.parent_seen.add(containing_namespace_obj_id)
            children_to_skip = self._get_children_to_skip(dsym, obj_id=containing_namespace_obj_id)
            for alias in self.safety.aliases[containing_namespace_obj_id]:
                # print('propagate from ns parent', alias, 'with refresh=', refresh)
                if refresh and not dsym.is_stale:
                    alias.namespace_stale_symbols.discard(dsym)
                    if not alias.is_stale:
                        alias.fresher_ancestors = set()
                self._propagate_update_to_namespace_parents(alias, refresh)
                if not refresh and dsym.should_mark_stale(self.updated_sym):
                    # print(self, 'mark stale due to', updated_dep, 'which is in namespace of', alias)
                    alias.namespace_stale_symbols.add(dsym)
                if refresh:
                    # containing_scope.max_defined_timestamp = cell_counter()
                    self.safety.updated_scopes.add(containing_scope)
                for alias_child in alias.children:
                    if alias_child.obj_id == containing_namespace_obj_id or alias_child in children_to_skip:
                        continue
                    # should_refresh = containing_namespace_obj_id == getattr(alias_child.containing_scope, 'obj_id', None)
                    # if alias_child.obj_id == alias_child.cached_obj_id and alias_child in children_to_skip:
                    #     continue
                    # Next, complicated check to avoid propagating along a class -> instance edge.
                    # The only time this is OK is when we changed the class, which will not be the case here.
                    alias_child_namespace = alias_child.namespace
                    if alias_child_namespace is not None:
                        if alias_child_namespace.cloned_from is containing_scope:
                            if self.updated_sym.namespace is not containing_scope:
                                continue
                    # if len(self.safety.aliases[alias_child.obj_id] & seen) > 0:
                    #     continue
                    # print(self, alias, self.containing_scope, containing_namespace_obj_id, 'propagate to', alias_child, refresh, alias.is_stale, children_to_skip)
                    self._propagate_update(alias_child, alias_child._get_obj())
