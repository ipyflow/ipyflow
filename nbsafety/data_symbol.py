# -*- coding: utf-8 -*-
import logging
from typing import cast, TYPE_CHECKING
import weakref

from .ipython_utils import cell_counter

if TYPE_CHECKING:
    from typing import Any, Optional, Set, Union
    from .safety import DependencySafety
    from .scope import Scope

logger = logging.getLogger(__name__)

NOT_FOUND = object()


class DataSymbol(object):
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
        self._has_weakref = True
        # try:
        #     self.obj_ref = weakref.ref(obj)
        # except TypeError:
        #     self._has_weakref = False
        #     self.obj_ref = obj
        # self.cached_obj_ref = self.obj_ref
        # self._cached_has_weakref = self._has_weakref
        self._obj = obj
        self._cached_obj = obj
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
        return f'<{self.__class__.__name__} for variable {self.readable_name}>'

    def __str__(self):
        return self.readable_name

    # def _get_obj(self):
    #     if self._has_weakref:
    #         return self.obj_ref()
    #     else:
    #         return self.obj_ref
    #
    # def _get_cached_obj(self):
    #     if self._cached_has_weakref:
    #         return self.cached_obj_ref()
    #     else:
    #         return self.cached_obj_ref

    def shallow_clone(self, new_obj, new_containing_scope, **extra_kwargs):
        return self.__class__(self.name, new_obj, new_containing_scope, self.safety, **extra_kwargs)

    @property
    def obj_id(self):
        return id(self._obj)
        # return id(self._get_obj())

    @property
    def cached_obj_id(self):
        return id(self._cached_obj)
        # return id(self._get_cached_obj())

    @property
    def obj_type(self):
        return type(self._obj)
        # return type(self._get_obj())

    @property
    def cached_obj_type(self):
        return type(self._cached_obj)
        # return type(self._get_cached_obj())

    def update_obj_ref(self, obj):
        self._obj = obj
        # try:
        #     self.obj_ref = weakref.ref(obj)
        #     self._has_weakref = True
        # except TypeError:
        #     self.obj_ref = obj
        #     self._has_weakref = False

    def update_deps(
            self,
            new_deps: 'Set[DataSymbol]',
            overwrite=True,
            propagate_to_children=True,
    ):
        self.fresher_ancestors = set()
        self.namespace_data_syms_with_stale = set()
        self.defined_cell_num = cell_counter()
        self.required_cell_num = self.defined_cell_num
        if overwrite:
            for parent in self.parents - new_deps:
                parent.children.discard(self)
            self.parents = set()

        for new_parent in new_deps - self.parents:
            if new_parent is None:
                continue
            new_parent.children.add(self)
            self.parents.add(new_parent)

        self.defined_cell_num = cell_counter()
        self.namespace_data_syms_with_stale.discard(self)
        if propagate_to_children:
            self._propagate_update(self, set(), set(), set())
        self._cached_obj = self._obj
        # self.cached_obj_ref = self.obj_ref
        # self._cached_has_weakref = self._has_weakref

    def _propagate_update(
            self,
            updated_dep: 'DataSymbol',
            seen,
            parent_seen,
            child_seen,
            do_namespace_propagation=True
    ):
        if self in seen:
            return
        seen.add(self)
        if updated_dep is not self:
            self.fresher_ancestors.add(updated_dep)
            self.required_cell_num = updated_dep.defined_cell_num
            # print('mark', self, 'as stale due to', updated_dep)
        for child in self.children:
            child._propagate_update(updated_dep, seen, parent_seen, child_seen)
        if do_namespace_propagation:
            self._propagate_update_to_namespace_children(self._cached_obj, self._obj, updated_dep,
                                                         seen, parent_seen, child_seen,
                                                         toplevel=True, refresh=updated_dep is self)
            self._propagate_update_to_namespace_parents(updated_dep, seen, parent_seen, child_seen,
                                                        refresh=updated_dep is self)

    def _propagate_update_to_namespace_children(
            self, old_parent_obj: 'Any', new_parent_obj: 'Any',
            updated_dep: 'DataSymbol', seen, parent_seen, child_seen,
            toplevel=False, refresh=False
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
        if self in child_seen:
            return
        old_id = id(old_parent_obj)
        if new_parent_obj is NOT_FOUND:
            new_id = None
        else:
            new_id = id(new_parent_obj)
        child_seen.add(self)
        if not toplevel:
            if refresh:
                for alias in self.safety.aliases[old_id]:
                    if alias.defined_cell_num < alias.required_cell_num < cell_counter():
                        logger.warning('possible stale usage of namespace descendent %s' % alias)
                    if new_parent_obj is not old_parent_obj:
                        alias._propagate_update(
                            updated_dep, seen, parent_seen, child_seen, do_namespace_propagation=False
                        )
                    alias.defined_cell_num = alias.required_cell_num
                    # print('mark', alias, 'as fresh')
            else:
                self._propagate_update(updated_dep, seen, parent_seen, child_seen, do_namespace_propagation=False)
                if new_parent_obj is NOT_FOUND or old_parent_obj is not new_parent_obj:
                    for alias in self.safety.aliases[old_id]:
                        alias._propagate_update(
                            updated_dep, seen, parent_seen, child_seen, do_namespace_propagation=False
                        )

        namespace = self.safety.namespaces.get(old_id, None)
        if namespace is None:
            return
        for dc in namespace._data_symbol_by_name.values():
            if new_parent_obj is NOT_FOUND:
                dc._propagate_update_to_namespace_children(
                    dc._obj, NOT_FOUND, updated_dep, seen, parent_seen, child_seen, refresh=refresh
                )
            else:
                try:
                    obj = self._obj
                    if dc.is_subscript:
                        # TODO: more complete list of things that are checkable
                        #  or could cause side effects upon subscripting
                        if isinstance(obj, dict) and dc.name not in obj:
                            raise KeyError()
                        obj = obj[dc.name]
                        dc._propagate_update_to_namespace_children(
                            dc._obj, obj, updated_dep, seen, parent_seen, child_seen, refresh=refresh
                        )
                    else:
                        dc_string_name = cast(str, dc.name)
                        if not hasattr(obj, dc_string_name):
                            raise AttributeError()
                        obj = getattr(obj, dc_string_name)
                        dc._propagate_update_to_namespace_children(
                            dc._obj, obj, updated_dep, seen, parent_seen, child_seen, refresh=refresh
                        )
                    if new_parent_obj is not old_parent_obj and new_id is not None:
                        new_namespace = self.safety.namespaces.get(new_id, None)
                        if new_namespace is None:
                            new_namespace = namespace.shallow_clone(new_id)
                            self.safety.namespaces[new_id] = new_namespace
                        # TODO: handle class data cells properly;
                        #  in fact; we still need to handle aliases of class data cells
                        if dc.name not in new_namespace._data_symbol_by_name:
                            new_namespace.put(dc.name, dc.shallow_clone(obj, new_namespace))
                except:
                    dc._propagate_update_to_namespace_children(dc._obj, NOT_FOUND, updated_dep, seen,
                                                               parent_seen, child_seen, refresh=refresh)
            if not dc.has_stale_ancestor:
                self.namespace_data_syms_with_stale.discard(dc)

    def mark_mutated(self, propagate_to_children=True):
        # print(self, 'mark mutated')
        self.update_deps(set(), overwrite=False, propagate_to_children=propagate_to_children)

    def _propagate_update_to_namespace_parents(self, updated_dep, seen, parent_seen, child_seen, refresh):
        if not self.containing_scope.is_namespace_scope:
            return
        if self in parent_seen:
            return
        parent_seen.add(self)
        containing_scope = cast('NamespaceScope', self.containing_scope)
        containing_scope.max_defined_timestamp = max(
            updated_dep.defined_cell_num, containing_scope.max_defined_timestamp)
        namespace_obj_ref = containing_scope.namespace_obj_ref
        for alias in self.safety.aliases[namespace_obj_ref]:
            if refresh and not self.has_stale_ancestor:
                alias.namespace_data_syms_with_stale.discard(self)
                if not alias.has_stale_ancestor:
                    alias.fresher_ancestors = set()
            if refresh:
                alias._propagate_update_to_namespace_parents(updated_dep, seen, parent_seen, child_seen, refresh)
                for alias_child in alias.children:
                    if alias_child.obj_id != namespace_obj_ref:
                        alias_child._propagate_update(updated_dep, seen, parent_seen, child_seen)
            else:
                alias.namespace_data_syms_with_stale.add(self)
                old_required_cell_num = alias.required_cell_num
                alias._propagate_update(updated_dep, seen, parent_seen, child_seen)
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
