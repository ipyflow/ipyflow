# -*- coding: utf-8 -*-
import logging
from typing import cast, TYPE_CHECKING

from .ipython_utils import cell_counter
from .utils.utils import retrieve_namespace_attr_or_sub

if TYPE_CHECKING:
    from typing import Any, Set
    from .data_symbol import DataSymbol
    from .scope import NamespaceScope

logger = logging.getLogger(__name__)
NOT_FOUND = object()


class LegacyUpdateProtocolMixin(object):
    def update_deps(
            self: 'DataSymbol',
            new_deps: 'Set[DataSymbol]',
            overwrite=True,
            mutated=False,
            propagate=True
    ):
        # quick last fix to avoid overwriting if we appear inside the set of deps to add
        overwrite = overwrite and self not in new_deps
        new_deps.discard(self)
        if overwrite:
            for parent in self.parents - new_deps:
                parent.children.discard(self)
            self.parents = set()

        for new_parent in new_deps - self.parents:
            if new_parent is None:
                continue
            new_parent.children.add(self)
            self.parents.add(new_parent)

        self.required_cell_num = -1
        if propagate:
            # print(self, 'update deps')
            self._propagate_update(self._get_obj(), self, set(), set(), refresh=True, mutated=mutated)
        self._refresh_cached_obj()
        self.safety.updated_symbols.add(self)

    def refresh(self: 'DataSymbol'):
        self.fresher_ancestors = set()
        self.defined_cell_num = cell_counter()
        self.required_cell_num = self.defined_cell_num
        self.namespace_stale_symbols = set()
        self._propagate_refresh_to_namespace_parents(set())

    def _propagate_update_to_deps(
            self: 'DataSymbol',
            updated_dep: 'DataSymbol',
            seen: 'Set[DataSymbol]',
            parent_seen: 'Set[DataSymbol]'
    ):
        # print(self, 'propagate to child deps', self.children)
        if self.should_mark_stale(updated_dep):
            # if self.full_namespace_path == updated_dep.full_namespace_path:
            #     print('weird', self.full_namespace_path, self, updated_dep, self.obj_id, updated_dep.obj_id, self.cached_obj_id, updated_dep.cached_obj_id)
            self.fresher_ancestors.add(updated_dep)
            self.required_cell_num = cell_counter()
        for child in self.children:
            # print(self, 'prop to', child, self._get_children_to_skip())
            child._propagate_update(child._get_obj(), updated_dep, seen, parent_seen)

    def _get_children_to_skip(self: 'DataSymbol', obj_id=None):
        # TODO: this should probably DFS in order to skip all namespace ancestors
        if obj_id is None:
            obj_id = self.obj_id
        children_to_skip = set()
        # skip any children that themselves contain an alias of self
        # TODO: this definitely won't work unless we are aware of containing namespaces
        #  need to therefore create data symbols for list literals and things like lst `append`
        for self_alias in self.safety.aliases[obj_id]:
            containing_obj_id = getattr(self_alias.containing_scope, 'obj_id', None)
            if containing_obj_id is not None:
                children_to_skip |= self.safety.aliases[containing_obj_id]
        return children_to_skip

    def _propagate_update(
            self: 'DataSymbol',
            new_parent_obj: 'Any',
            updated_dep: 'DataSymbol', seen, parent_seen,
            refresh=False, mutated=False
    ):
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
        if self._tombstone or self in seen:
            return
        seen.add(self)
        # print('propagate update from', self)

        new_id = None if new_parent_obj is NOT_FOUND else id(new_parent_obj)

        if updated_dep is self:
            old_parent_obj = self._get_cached_obj()
            old_id = self.cached_obj_id
        else:
            old_parent_obj = self._get_obj()
            old_id = self.obj_id

        namespace = self.safety.namespaces.get(old_id, None)
        if namespace is None and refresh:  # if we are at a leaf
            self._propagate_update_to_namespace_parents(updated_dep, seen, parent_seen, refresh=refresh)
        if namespace is not None and (old_id != new_id or mutated or not refresh):
            for dc in namespace.all_data_symbols_this_indentation(exclude_class=True):
                should_refresh = refresh  # or updated_dep in set(namespace.all_data_symbols_this_indentation())
                dc_in_self_namespace = False
                if new_parent_obj is NOT_FOUND:
                    dc._propagate_update(NOT_FOUND, updated_dep, seen, parent_seen, refresh=should_refresh, mutated=mutated)
                else:
                    try:
                        obj = self._get_obj()
                        obj_attr_or_sub = retrieve_namespace_attr_or_sub(obj, dc.name, dc.is_subscript)
                        # print(dc, obj, obj_attr_or_sub, updated_dep, seen, parent_seen, refresh, mutated, old_id, new_id)
                        dc._propagate_update(
                            obj_attr_or_sub, updated_dep, seen, parent_seen, refresh=should_refresh, mutated=mutated
                        )
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
                        dc._propagate_update(
                            NOT_FOUND, updated_dep, seen, parent_seen, refresh=should_refresh, mutated=mutated
                        )
                if dc_in_self_namespace and dc.should_mark_stale(updated_dep):
                    # print(self, 'add', dc, 'to namespace stale symbols due to', updated_dep, self.defined_cell_num, dc.defined_cell_num, updated_dep.defined_cell_num, dc.fresher_ancestors)
                    self.namespace_stale_symbols.add(dc)
                else:
                    self.namespace_stale_symbols.discard(dc)

        # if mutated or self.cached_obj_id != self.obj_id:
        # if (old_id != new_id or not refresh) and not mutated:
        #     self._propagate_update_to_deps(updated_dep, seen, parent_seen)
        # elif mutated:
        if old_id != new_id or not refresh or mutated:
            to_skip = self._get_children_to_skip()
            self._propagate_update_to_deps(updated_dep, seen | to_skip, parent_seen)
            # print(self, 'propagate+skip done')

        if updated_dep is self:
            return

        if refresh:
            for alias in self.safety.aliases[old_id]:
                if alias.defined_cell_num < alias.required_cell_num < cell_counter():
                    logger.debug('possible stale usage of namespace descendent %s' % alias)
                if len(alias.namespace_stale_symbols) > 0:
                    logger.debug('unexpected stale namespace symbols for symbol %s: %s' % (alias, alias.namespace_stale_symbols))
                    alias.namespace_stale_symbols.clear()
                if old_id != new_id or mutated:
                    # TODO: better equality testing
                    #  Doing equality testing properly requires that we still have a reference to old object around;
                    #  we should be using weak refs, which complicates this.
                    #  Depth also makes this challenging.
                    alias._propagate_update_to_deps(updated_dep, seen, parent_seen)
                # TODO: this will probably work, but will also unnecessarily propagate the refresh up to
                #  namespace parents. Need a mechanism to mark for refresh without parent propagation.
                self.safety.updated_symbols.add(alias)
        else:
            # propagating to all aliases was causing problems
            # see test `test_class_redef`
            self._propagate_update_to_deps(updated_dep, seen, parent_seen)
            # for alias in self.safety.aliases[old_id]:
            #     # print('propagate', updated_dep, 'to', alias, 'via', self, updated_dep.defined_cell_num, alias.defined_cell_num, self.defined_cell_num)
            #     alias._propagate_update_to_deps(updated_dep, seen, parent_seen)
            if namespace is None and self.should_mark_stale(updated_dep):  # if we are at a leaf
                # print('propagate', updated_dep, 'to', self, updated_dep.defined_cell_num, self.defined_cell_num)
                self._propagate_update_to_namespace_parents(updated_dep, seen, parent_seen, refresh=refresh)
        # print(self, 'done')

    def mark_mutated(self: 'DataSymbol'):
        # print(self, 'mark mutated')
        self.update_deps(set(), overwrite=False, mutated=True)

    def _propagate_refresh_to_namespace_parents(self: 'DataSymbol', seen):
        if not self.containing_scope.is_namespace_scope or self in seen:
            return
        # print('refresh propagate', self)
        seen.add(self)
        containing_scope: 'NamespaceScope' = cast('NamespaceScope', self.containing_scope)
        if containing_scope.max_defined_timestamp == cell_counter():
            return
        containing_scope.max_defined_timestamp = cell_counter()
        containing_namespace_obj_id = containing_scope.obj_id
        for alias in self.safety.aliases[containing_namespace_obj_id]:
            alias.namespace_stale_symbols.discard(self)
            if not alias.is_stale:
                alias.fresher_ancestors = set()
                alias._propagate_refresh_to_namespace_parents(seen)

    def _propagate_update_to_namespace_parents(self: 'DataSymbol', updated_dep, seen, parent_seen, refresh):
        if not self.containing_scope.is_namespace_scope or self in parent_seen:
            return
        # print('parent propagate', self)
        parent_seen.add(self)
        containing_scope = cast('NamespaceScope', self.containing_scope)
        containing_namespace_obj_ids_to_consider = {containing_scope.obj_id}
        # if refresh:
        #     for self_alias in self.safety.aliases[self.obj_id]:
        #         if self_alias.containing_scope.is_namespace_scope:
        #             containing_namespace_obj_ids_to_consider.add(getattr(self_alias.containing_scope, 'obj_id', None))
        #     containing_namespace_obj_ids_to_consider.discard(None)
        for containing_namespace_obj_id in containing_namespace_obj_ids_to_consider:
            if containing_namespace_obj_id in parent_seen:
                continue
            parent_seen.add(containing_namespace_obj_id)
            children_to_skip = self._get_children_to_skip(containing_namespace_obj_id)
            for alias in self.safety.aliases[containing_namespace_obj_id]:
                # print('propagate from ns parent', alias, 'with refresh=', refresh)
                if refresh and not self.is_stale:
                    alias.namespace_stale_symbols.discard(self)
                    if not alias.is_stale:
                        alias.fresher_ancestors = set()
                alias._propagate_update_to_namespace_parents(updated_dep, seen, parent_seen, refresh)
                if not refresh and self.should_mark_stale(updated_dep):
                    # print(self, 'mark stale due to', updated_dep, 'which is in namespace of', alias)
                    alias.namespace_stale_symbols.add(self)
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
                            if updated_dep.namespace is not containing_scope:
                                continue
                    # if len(self.safety.aliases[alias_child.obj_id] & seen) > 0:
                    #     continue
                    # print(self, alias, self.containing_scope, containing_namespace_obj_id, 'propagate to', alias_child, refresh, alias.is_stale, children_to_skip)
                    alias_child._propagate_update(alias_child._get_obj(), updated_dep, seen, parent_seen)
