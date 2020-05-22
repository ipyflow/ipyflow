# -*- coding: utf-8 -*-
import inspect
from typing import TYPE_CHECKING

from IPython import get_ipython
try:
    import pandas
except ImportError:
    pandas = None

from .analysis import AttrSubSymbolChain, CallPoint
from .data_cell import ClassDataCell, DataCell, FunctionDataCell

if TYPE_CHECKING:
    from typing import Any, Dict, Optional, Set, Tuple, Union


class Scope(object):
    GLOBAL_SCOPE_NAME = '<module>'

    def __init__(
            self, scope_name: str = GLOBAL_SCOPE_NAME,
            parent_scope: 'Optional[Scope]' = None,
    ):
        self.scope_name = scope_name
        self.parent_scope = parent_scope  # None iff this is the global scope
        self._data_cell_by_name: Dict[str, DataCell] = {}

    def __hash__(self):
        return hash(self.full_path)

    def __str__(self):
        return str(self.full_path)

    @property
    def is_namespace_scope(self):
        return isinstance(self, NamespaceScope)

    @property
    def non_namespace_parent_scope(self):
        # a scope nested inside of a namespace scope does not have access
        # to unqualified members of the namespace scope
        if self.is_global:
            return None
        if self.parent_scope.is_namespace_scope:
            return self.parent_scope.non_namespace_parent_scope
        return self.parent_scope

    def make_child_scope(self, scope_name, namespace_obj_ref=None):
        if namespace_obj_ref is None:
            return Scope(scope_name, parent_scope=self)
        else:
            return NamespaceScope(namespace_obj_ref, scope_name, parent_scope=self)

    def put(self, name: str, val: DataCell):
        self._data_cell_by_name[name] = val

    def lookup_data_cell_by_name_this_indentation(self, name):
        return self._data_cell_by_name.get(name, None)

    def all_data_cells_this_indentation(self):
        return self._data_cell_by_name

    def lookup_data_cell_by_name(self, name):
        ret = self.lookup_data_cell_by_name_this_indentation(name)
        if ret is None and self.non_namespace_parent_scope is not None:
            ret = self.non_namespace_parent_scope.lookup_data_cell_by_name(name)
        return ret

    def gen_data_cells_for_attr_symbol_chain(self, chain: AttrSubSymbolChain, namespaces: 'Dict[int, Scope]'):
        cur_scope = self
        name_to_obj = get_ipython().ns_table['user_global']
        dc = None
        # TODO: change `yield` to `return` after testing this
        for name in chain.symbols:
            if isinstance(name, CallPoint):
                break
            dc = cur_scope.lookup_data_cell_by_name_this_indentation(name)
            # if dc is not None:
            #     yield dc
            if name_to_obj is None:
                break
            obj = name_to_obj.get(name, None)
            if obj is None:
                break
            cur_scope = namespaces.get(id(obj), None)
            if cur_scope is None:
                break

            try:
                name_to_obj = obj.__dict__
                if (pandas is not None) and isinstance(obj, pandas.DataFrame):
                    # FIXME: hack to get it working w/ pandas, which doesn't play nicely w/ inspect.getmembers
                    name_to_obj = dict(name_to_obj)
                    name_to_obj.update(obj.to_dict())
            except:  # noqa
                try:
                    name_to_obj = inspect.getmembers(obj)
                except:  # noqa
                    name_to_obj = None
        if dc is not None:
            yield dc

    def _upsert_and_mark_children_if_same_data_cell_type(
            self, dc: 'Union[ClassDataCell, FunctionDataCell]', name: str, deps: 'Set[DataCell]'
    ) -> 'Tuple[DataCell, DataCell, Optional[int]]':
        old_id = None
        old_dc = None
        should_propagate = False
        if self.is_globally_accessible:
            old_dc = self.lookup_data_cell_by_name_this_indentation(name)
            if old_dc is not None:
                for child in old_dc.children:
                    child.parents.discard(old_dc)
                    child.fresher_ancestors.discard(old_dc)
                old_id = old_dc.cached_obj_id
                # don't mark children as having stale dep unless old dep was of same type
                should_propagate = isinstance(old_dc, type(dc))
        if should_propagate and old_dc is not None:
            dc.children = old_dc.children
            for child in dc.children:
                child.parents.add(dc)
        dc.update_deps(deps, add=False)
        self.put(name, dc)
        return dc, old_dc, old_id

    def _upsert_function_data_cell_for_name(self, name: str, obj: 'Any', deps: 'Set[DataCell]'):
        dc = FunctionDataCell(self.make_child_scope(name), name, obj, self)
        return self._upsert_and_mark_children_if_same_data_cell_type(dc, name, deps)

    def _upsert_class_data_cell_for_name(self, name: str, obj: 'Any', deps: 'Set[DataCell]', class_scope: 'Scope'):
        dc = ClassDataCell(class_scope, name, obj, self)
        return self._upsert_and_mark_children_if_same_data_cell_type(dc, name, deps)

    def upsert_data_cell_for_name(
            self,
            name: str,
            obj: 'Any',
            deps: 'Set[DataCell]',
            is_subscript,
            add=False,
            is_function_def=False,
            class_scope: 'Optional[Scope]' = None,
    ) -> 'Tuple[DataCell, DataCell, Optional[int]]':
        assert not (class_scope is not None and is_function_def)
        if is_function_def:
            assert not add
            assert not is_subscript
            return self._upsert_function_data_cell_for_name(name, obj, deps)
        if class_scope is not None:
            assert not add
            assert not is_subscript
            return self._upsert_class_data_cell_for_name(name, obj, deps, class_scope)
        old_id = None
        old_dc = None
        if self.is_globally_accessible:
            old_dc = self.lookup_data_cell_by_name_this_indentation(name)
            if old_dc is not None:
                old_id = old_dc.cached_obj_id
                # TODO: garbage collect old names
                # TODO: handle case where new dc is of different type
                if name in self._data_cell_by_name:
                    old_dc.update_deps(deps, add=add)
                    old_dc.update_obj_ref(obj)
                    return old_dc, old_dc, old_id
                else:
                    # in this case, we are copying from a class and should add the dc from which we are copying
                    # as an additional dependency
                    deps.add(old_dc)
        dc = DataCell(name, obj, self, deps, is_subscript=is_subscript)
        self.put(name, dc)
        for dep in deps:
            dep.children.add(dc)
        return dc, old_dc, old_id

    @property
    def is_global(self):
        return self.parent_scope is None

    @property
    def is_globally_accessible(self):
        return self.is_global or (self.is_namespace_scope and self.parent_scope.is_globally_accessible)

    @property
    def global_scope(self):
        if self.is_global:
            return self
        return self.parent_scope.global_scope

    @property
    def full_path(self) -> 'Tuple[str, ...]':
        path = (self.scope_name,)
        if self.is_global:
            return path
        else:
            return self.parent_scope.full_path + path

    @property
    def full_namespace_path(self) -> str:
        if not self.is_namespace_scope:
            return ''
        if self.parent_scope is not None:
            prefix = self.parent_scope.full_namespace_path
        else:
            prefix = ''
        if prefix:
            return f'{prefix}.{self.scope_name}'
        else:
            return self.scope_name

    def make_namespace_qualified_name(self, dc: 'DataCell'):
        return dc.name


class NamespaceScope(Scope):
    def __init__(self, namespace_obj_ref: int, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cloned_from: Optional[Scope] = None
        self.namespace_obj_ref = namespace_obj_ref

    def clone(self, namespace_obj_ref: int):
        cloned = NamespaceScope(namespace_obj_ref)
        cloned.__dict__ = dict(self.__dict__)
        cloned.cloned_from = self
        cloned.namespace_obj_ref = namespace_obj_ref
        cloned._data_cell_by_name = {}
        return cloned

    def make_namespace_qualified_name(self, dc: 'DataCell'):
        path = self.full_namespace_path
        if path:
            if dc.is_subscript:
                return f'{path}[{dc.name}]'
            else:
                return f'{path}.{dc.name}'
        else:
            return dc.name

    def lookup_data_cell_by_name_this_indentation(self, name):
        ret = self._data_cell_by_name.get(name, None)
        if ret is None and self.cloned_from is not None:
            ret = self.cloned_from.lookup_data_cell_by_name_this_indentation(name)
        return ret

    def all_data_cells_this_indentation(self):
        ret = self.cloned_from.all_data_cells_this_indentation()
        ret.update(self._data_cell_by_name)
        return ret
