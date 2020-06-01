# -*- coding: utf-8 -*-
from enum import Enum
import logging
from typing import cast, TYPE_CHECKING
import weakref

from .ipython_utils import cell_counter
from .utils import retrieve_namespace_attr_or_sub

if TYPE_CHECKING:
    from typing import Any, Optional, Set, Union
    from .safety import DependencySafety
    from .scope import Scope, NamespaceScope

logger = logging.getLogger(__name__)

NOT_FOUND = object()


class DataSymbolType(Enum):
    DEFAULT = 'default'
    SUBSCRIPT = 'subscript'
    FUNCTION = 'function'
    CLASS = 'class'


class DataSymbol(object):
    def __init__(
            self,
            name: 'Union[str, int]',
            symbol_type: 'DataSymbolType',
            obj: 'Any',
            containing_scope: 'Scope',
            safety: 'DependencySafety',
            parents: 'Optional[Set[DataSymbol]]' = None,
    ):
        # print(containing_scope, name, obj, is_subscript)
        self.name = name
        self.symbol_type = symbol_type
        tombstone, obj_ref, has_weakref = self._update_obj_ref_inner(obj)
        self._tombstone = tombstone
        self._obj_ref = obj_ref
        self._has_weakref = has_weakref
        self.cached_obj_ref = None
        self._cached_has_weakref = None
        self.cached_obj_id = None
        self.cached_obj_type = None
        self.containing_scope = containing_scope
        self.safety = safety
        if parents is None:
            parents = set()
        self.parents: Set[DataSymbol] = parents
        self.children: Set[DataSymbol] = set()
        self.readable_name = containing_scope.make_namespace_qualified_name(self)

        if self.is_function:
            self.call_scope = self.containing_scope.make_child_scope(self.name)
        else:
            self.call_scope = None

        self.defined_cell_num = cell_counter()

        # The notebook cell number this is required to have to not be considered stale
        self.required_cell_num = self.defined_cell_num

        self.fresher_ancestors: Set[DataSymbol] = set()
        self.namespace_data_syms_with_stale: Set[DataSymbol] = set()

        # Will never be stale if no_warning is True
        self.disable_warnings = False

    def __repr__(self):
        return f'<{self.readable_name}>'

    def __str__(self):
        return self.readable_name

    @property
    def is_subscript(self):
        return self.symbol_type == DataSymbolType.SUBSCRIPT

    @property
    def is_class(self):
        return self.symbol_type == DataSymbolType.CLASS

    @property
    def is_function(self):
        return self.symbol_type == DataSymbolType.FUNCTION

    def _get_obj(self) -> 'Any':
        if self._has_weakref:
            return self._obj_ref()
        else:
            return self._obj_ref

    def _get_cached_obj(self) -> 'Any':
        if self._cached_has_weakref:
            return self.cached_obj_ref()
        else:
            return self.cached_obj_ref

    def shallow_clone(self, new_obj, new_containing_scope, symbol_type):
        return self.__class__(self.name, symbol_type, new_obj, new_containing_scope, self.safety)

    @property
    def obj_id(self):
        return id(self._get_obj())

    @property
    def obj_type(self):
        return type(self._get_obj())

    @property
    def namespace(self):
        return self.safety.namespaces.get(self.obj_id, None)

    @property
    def is_garbage(self):
        return (
            self._tombstone
            or not self.containing_scope.is_globally_accessible
            or (self._has_weakref and self._get_obj() is None)
        )

    def _obj_reference_expired_callback(self, *_):
        # just write a tombstone here; we'll do a batch collect after the main part of the cell is done running
        # can potentially support GC in the background further down the line
        self._tombstone = True

    def collect_self_garbage(self):
        for parent in self.parents:
            parent.children.discard(self)
        for child in self.children:
            child.parents.discard(self)
        self_aliases = self.safety.aliases.get(self.cached_obj_id, None)
        if self_aliases is not None:
            self_aliases.discard(self)
            if len(self_aliases) == 0:
                # kill the alias but leave the namespace
                # namespace needs to stick around to properly handle the staleness propagation protocol
                self.safety.aliases.pop(self.cached_obj_id, None)

    def update_type(self, new_type):
        self.symbol_type = new_type
        if self.is_function:
            self.call_scope = self.containing_scope.make_child_scope(self.name)
        else:
            self.call_scope = None

    def update_obj_ref(self, obj):
        tombstone, obj_ref, has_weakref = self._update_obj_ref_inner(obj)
        self._tombstone = tombstone
        self._obj_ref = obj_ref
        self._has_weakref = has_weakref

    def _update_obj_ref_inner(self, obj):
        tombstone = False
        try:
            obj_ref = weakref.ref(obj, self._obj_reference_expired_callback)
            has_weakref = True
        except TypeError:
            obj_ref = obj
            has_weakref = False
        return tombstone, obj_ref, has_weakref

    def _refresh_cached_obj(self):
        self.cached_obj_ref = self._obj_ref
        self.cached_obj_id = self.obj_id
        self.cached_obj_type = self.obj_type
        self._cached_has_weakref = self._has_weakref

    def update_deps(
            self,
            new_deps: 'Set[DataSymbol]',
            overwrite=True,
            mutated=False,
    ):

        # quick last fix to avoid ovewriting if we appear inside the set of deps to add
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
        self._propagate_update(self._get_obj(), self, set(), set(), refresh=True, mutated=mutated)
        self._refresh_cached_obj()
        self.safety.updated_symbols.add(self)

    def refresh(self):
        self.fresher_ancestors = set()
        self.defined_cell_num = cell_counter()
        self.required_cell_num = self.defined_cell_num
        self.namespace_data_syms_with_stale = set()
        self._propagate_refresh_to_namespace_parents(set())

    def _propagate_update_to_deps(
            self,
            updated_dep: 'DataSymbol',
            seen: 'Set[DataSymbol]',
            parent_seen: 'Set[DataSymbol]',
    ):
        if self.should_mark_stale(updated_dep):
            self.fresher_ancestors.add(updated_dep)
            self.required_cell_num = cell_counter()
        for child in self.children:
            child._propagate_update(child._get_obj(), updated_dep, seen, parent_seen)

    def _propagate_update(
            self, new_parent_obj: 'Any',
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
        for dc in [] if namespace is None else namespace.all_data_symbols_this_indentation():
            dc_in_self_namespace = False
            if new_parent_obj is NOT_FOUND:
                dc._propagate_update(NOT_FOUND, updated_dep, seen, parent_seen, refresh=refresh, mutated=mutated)
            else:
                try:
                    obj = self._get_obj()
                    obj_attr_or_sub = retrieve_namespace_attr_or_sub(obj, dc.name, dc.is_subscript)
                    dc._propagate_update(
                        obj_attr_or_sub, updated_dep, seen, parent_seen, refresh=refresh, mutated=mutated
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
                    dc._propagate_update(NOT_FOUND, updated_dep, seen, parent_seen, refresh=refresh, mutated=mutated)
            if dc_in_self_namespace and dc.has_stale_ancestor:
                if dc.should_mark_stale(updated_dep) and self.should_mark_stale(updated_dep):
                    self.namespace_data_syms_with_stale.add(dc)
            else:
                self.namespace_data_syms_with_stale.discard(dc)

        if mutated or self.cached_obj_id != self.obj_id:
            self._propagate_update_to_deps(updated_dep, seen, parent_seen)

        if updated_dep is self:
            return

        if refresh:
            for alias in self.safety.aliases[old_id]:
                if alias.defined_cell_num < alias.required_cell_num < cell_counter():
                    logger.warning('possible stale usage of namespace descendent %s' % alias)
                if len(alias.namespace_data_syms_with_stale) > 0:
                    logger.warning('unexpected stale namespace symbols for symbol %s: %s' % (alias, alias.namespace_data_syms_with_stale))
                    alias.namespace_data_syms_with_stale.clear()
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
            for alias in self.safety.aliases[old_id]:
                # print('propagate', updated_dep, 'to', alias, 'via', self, updated_dep.defined_cell_num, alias.defined_cell_num, self.defined_cell_num)
                alias._propagate_update_to_deps(updated_dep, seen, parent_seen)
            if namespace is None and self.should_mark_stale(updated_dep):  # if we are at a leaf
                self._propagate_update_to_namespace_parents(updated_dep, seen, parent_seen, refresh=refresh)

    def mark_mutated(self):
        # print(self, 'mark mutated')
        self.update_deps(set(), overwrite=False, mutated=True)

    def _propagate_refresh_to_namespace_parents(self, seen):
        if not self.containing_scope.is_namespace_scope or self in seen:
            return
        seen.add(self)
        containing_scope: 'NamespaceScope' = cast('NamespaceScope', self.containing_scope)
        if containing_scope.max_defined_timestamp == cell_counter():
            return
        containing_scope.max_defined_timestamp = cell_counter()
        containing_namespace_obj_id = containing_scope.obj_id
        for alias in self.safety.aliases[containing_namespace_obj_id]:
            alias.namespace_data_syms_with_stale.discard(self)
            if not alias.has_stale_ancestor:
                alias.fresher_ancestors = set()
                alias._propagate_refresh_to_namespace_parents(seen)

    def _propagate_update_to_namespace_parents(self, updated_dep, seen, parent_seen, refresh):
        if not self.containing_scope.is_namespace_scope or self in parent_seen:
            return
        parent_seen.add(self)
        containing_scope = cast('NamespaceScope', self.containing_scope)
        containing_namespace_obj_id = containing_scope.obj_id
        for alias in self.safety.aliases[containing_namespace_obj_id]:
            if refresh and not self.has_stale_ancestor:
                alias.namespace_data_syms_with_stale.discard(self)
                if not alias.has_stale_ancestor:
                    alias.fresher_ancestors = set()
            if refresh:
                # containing_scope.max_defined_timestamp = cell_counter()
                self.safety.updated_scopes.add(containing_scope)
                alias._propagate_update_to_namespace_parents(updated_dep, seen, parent_seen, refresh)
                for alias_child in alias.children:
                    if alias_child.obj_id == containing_namespace_obj_id:
                        continue
                    # Next, complicated check to avoid propagating along a class -> instance edge.
                    # The only time this is OK is when we changed the class, which will not be the case here.
                    alias_child_namespace = alias_child.namespace
                    if alias_child_namespace is not None:
                        if alias_child_namespace.cloned_from is containing_scope:
                            if updated_dep.namespace is not containing_scope:
                                continue
                    alias_child._propagate_update(alias_child._get_obj(), updated_dep, seen, parent_seen)
            else:
                if self.should_mark_stale(updated_dep) and alias.should_mark_stale(updated_dep):
                    if containing_scope.max_defined_timestamp != updated_dep.defined_cell_num:
                        alias.namespace_data_syms_with_stale.add(self)
                old_required_cell_num = alias.required_cell_num
                alias._propagate_update(alias._get_obj(), updated_dep, seen, parent_seen)
                alias.required_cell_num = old_required_cell_num

    @property
    def has_stale_ancestor(self):
        if self.disable_warnings:
            return False
        return self.defined_cell_num < self.required_cell_num or len(self.namespace_data_syms_with_stale) > 0

    def should_mark_stale(self, updated_dep):
        if self.disable_warnings:
            return False
        if updated_dep is self:
            return False
        should_mark_stale = not self.safety.no_stale_propagation_for_same_cell_definition
        should_mark_stale = should_mark_stale or updated_dep.defined_cell_num != self.defined_cell_num
        return should_mark_stale
