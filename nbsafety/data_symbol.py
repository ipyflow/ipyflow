# -*- coding: utf-8 -*-
import logging
from typing import cast, TYPE_CHECKING
import weakref

from .ipython_utils import cell_counter
from .utils import retrieve_namespace_attr_or_sub

if TYPE_CHECKING:
    from typing import Any, Optional, Set, Union
    from .safety import DependencySafety
    from .scope import Scope

logger = logging.getLogger(__name__)

NOT_FOUND = object()


class DataSymbol(object):
    # TODO: make the is_subscript arg of datasym constructor required
    def __init__(
            self,
            name: 'Union[str, int]',
            obj: 'Any',
            containing_scope: 'Scope',
            safety: 'DependencySafety',
            parents: 'Optional[Set[DataSymbol]]' = None,
            is_subscript: bool = False
    ):
        self.name = name
        self.update_obj_ref(obj)
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
        self.is_subscript = is_subscript
        self.readable_name = containing_scope.make_namespace_qualified_name(self)

        self.defined_cell_num = cell_counter()

        # The notebook cell number this is required to have to not be considered stale
        self.required_cell_num = self.defined_cell_num

        self.fresher_ancestors: Set[DataSymbol] = set()
        self.namespace_data_syms_with_stale: Set[DataSymbol] = set()

        #Will never be stale if no_warning is True
        self.no_warning = False

    def __repr__(self):
        return f'<{self.readable_name}>'

    def __str__(self):
        return self.readable_name

    def _get_obj(self) -> 'Any':
        if self._has_weakref:
            return self.obj_ref()
        else:
            return self.obj_ref

    def _get_cached_obj(self) -> 'Any':
        if self._cached_has_weakref:
            return self.cached_obj_ref()
        else:
            return self.cached_obj_ref

    def shallow_clone(self, new_obj, new_containing_scope, **extra_kwargs):
        return self.__class__(self.name, new_obj, new_containing_scope, self.safety, **extra_kwargs)

    @property
    def obj_id(self):
        return id(self._get_obj())

    @property
    def obj_type(self):
        return type(self._get_obj())

    @property
    def namespace(self):
        return self.safety.namespaces.get(self.obj_id, None)

    def update_obj_ref(self, obj):
        try:
            self.obj_ref = weakref.ref(obj)
            self._has_weakref = True
        except TypeError:
            self.obj_ref = obj
            self._has_weakref = False

    def _refresh_cached_obj(self):
        self.cached_obj_ref = self.obj_ref
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
        self.fresher_ancestors = set()
        self.defined_cell_num = cell_counter()
        self.required_cell_num = self.defined_cell_num
        self.namespace_data_syms_with_stale = set()
        self._refresh_cached_obj()

    def _propagate_update_to_deps(
            self,
            updated_dep: 'DataSymbol',
            seen: 'Set[DataSymbol]',
            parent_seen: 'Set[DataSymbol]',
    ):
        if updated_dep is not self:
            self.fresher_ancestors.add(updated_dep)
            self.required_cell_num = cell_counter()
        for child in self.children:
            if self.safety.only_propagate_updates_past_cell_boundaries:
                if updated_dep is self and updated_dep.defined_cell_num == child.defined_cell_num:
                    continue
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
        if self in seen:
            return
        seen.add(self)
        if mutated or self.cached_obj_id != self.obj_id:
            self._propagate_update_to_deps(updated_dep, seen, parent_seen)

        new_id = None if new_parent_obj is NOT_FOUND else id(new_parent_obj)

        if updated_dep is self:
            old_parent_obj = self._get_cached_obj()
            old_id = self.cached_obj_id
        else:
            old_parent_obj = self._get_obj()
            old_id = self.obj_id
            if refresh:
                for alias in self.safety.aliases[old_id]:
                    if alias.defined_cell_num < alias.required_cell_num < cell_counter():
                        logger.warning('possible stale usage of namespace descendent %s' % alias)
                    if len(alias.namespace_data_syms_with_stale) > 0:
                        logger.warning('unexpected stale namespace symbols for symbol %s' % alias)
                        alias.namespace_data_syms_with_stale = set()
                    if old_id != new_id or mutated:
                        # TODO: better equality testing
                        #  Doing equality testing properly requires that we still have a reference to old object around;
                        #  we should be using weak refs, which complicates this.
                        #  Depth also makes this challenging.
                        alias._propagate_update_to_deps(updated_dep, seen, parent_seen)
                    alias.defined_cell_num = cell_counter()
            else:
                for alias in self.safety.aliases[old_id]:
                    alias._propagate_update_to_deps(updated_dep, seen, parent_seen)

        namespace = self.safety.namespaces.get(old_id, None)
        if namespace is None:
            self._propagate_update_to_namespace_parents(updated_dep, seen, parent_seen, refresh=refresh)
        for dc in [] if namespace is None else namespace.all_data_symbols_this_indentation(exclude_class=True):
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
                            new_namespace = namespace.shallow_clone(new_id)
                            self.safety.namespaces[new_id] = new_namespace
                        # TODO: handle class data cells properly;
                        #  in fact; we still need to handle aliases of class data cells
                        if dc.name not in new_namespace.data_symbol_by_name(dc.is_subscript):
                            new_namespace.put(dc.name, dc.shallow_clone(
                                obj_attr_or_sub, new_namespace, is_subscript=dc.is_subscript))
                except (KeyError, IndexError, AttributeError):
                    dc._propagate_update(NOT_FOUND, updated_dep, seen, parent_seen, refresh=refresh, mutated=mutated)
            if dc_in_self_namespace and dc.has_stale_ancestor:
                self.namespace_data_syms_with_stale.add(dc)
            else:
                self.namespace_data_syms_with_stale.discard(dc)

    def mark_mutated(self):
        # print(self, 'mark mutated')
        self.update_deps(set(), overwrite=False, mutated=True)

    def _propagate_update_to_namespace_parents(self, updated_dep, seen, parent_seen, refresh):
        if not self.containing_scope.is_namespace_scope or self in parent_seen:
            return
        parent_seen.add(self)
        containing_scope = cast('NamespaceScope', self.containing_scope)
        containing_scope.max_defined_timestamp = cell_counter()
        namespace_obj_ref = containing_scope.namespace_obj_ref
        for alias in self.safety.aliases[namespace_obj_ref]:
            if refresh and not self.has_stale_ancestor:
                alias.namespace_data_syms_with_stale.discard(self)
                if not alias.has_stale_ancestor:
                    alias.fresher_ancestors = set()
            if refresh:
                alias._propagate_update_to_namespace_parents(updated_dep, seen, parent_seen, refresh)
                for alias_child in alias.children:
                    if alias_child.obj_id == namespace_obj_ref:
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
                alias.namespace_data_syms_with_stale.add(self)
                old_required_cell_num = alias.required_cell_num
                alias._propagate_update(alias._get_obj(), updated_dep, seen, parent_seen)
                alias.required_cell_num = old_required_cell_num

    @property
    def has_stale_ancestor(self):
        if self.no_warning:
            return False
        return self.defined_cell_num < self.required_cell_num or len(self.namespace_data_syms_with_stale) > 0


class FunctionDataSymbol(DataSymbol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = self.containing_scope.make_child_scope(self.name)


class ClassDataSymbol(DataSymbol):
    def __init__(self, *args, **kwargs):
        class_scope = kwargs.pop('class_scope')
        super().__init__(*args, **kwargs)
        self.scope = class_scope
