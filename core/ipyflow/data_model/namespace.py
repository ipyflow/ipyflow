# -*- coding: utf-8 -*-
import itertools
import logging
from types import ModuleType
from typing import (
    Any,
    Dict,
    Generator,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from ipyflow.data_model.data_symbol import DataSymbol
from ipyflow.data_model.scope import Scope
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.singletons import flow
from ipyflow.types import SupportedIndexType

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class Namespace(Scope):
    ANONYMOUS = "<anonymous_namespace>"

    PENDING_CLASS_PLACEHOLDER = object()

    # special object for virtually representing the file system
    FILE_SYSTEM: Dict[str, None] = dict()

    # TODO: support (multiple) inheritance by allowing
    #  Namespaces from classes to clone their parent class's Namespaces
    def __init__(self, obj: Any, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cloned_from: Optional["Namespace"] = None
        self.child_clones: List["Namespace"] = []
        self.obj = obj
        self.cached_obj_id = id(obj)
        if (
            obj is not None
            and not isinstance(obj, int)
            and id(obj) in flow().namespaces
        ):  # pragma: no cover
            msg = "namespace already registered for %s" % obj
            if flow().is_develop:
                raise ValueError(msg)
            else:
                logger.warning(msg)
        if obj is not self.PENDING_CLASS_PLACEHOLDER:
            flow().namespaces[id(obj)] = self
        self._tombstone = False
        # this timestamp needs to be bumped in DataSymbol refresh()
        self.max_descendent_timestamp: Timestamp = Timestamp.uninitialized()
        self._subscript_data_symbol_by_name: Dict[SupportedIndexType, DataSymbol] = {}
        self.namespace_waiting_symbols: Set[DataSymbol] = set()

    @property
    def is_namespace_scope(self):
        return True

    def __bool__(self) -> bool:
        # in order to override if __len__ returns 0
        return True

    def __len__(self) -> int:
        if not isinstance(self.obj, (dict, list, tuple)):  # pragma: no cover
            raise TypeError(
                "tried to get length of non-container namespace %s: %s", self, self.obj
            )
        return len(self.obj)

    @property
    def size(self) -> int:
        return len(self._subscript_data_symbol_by_name) + len(self._data_symbol_by_name)

    def _iter_inner(self) -> Generator[Optional[DataSymbol], None, None]:
        for i in range(len(self.obj)):
            yield self.lookup_data_symbol_by_name_this_indentation(i, is_subscript=True)

    def __iter__(self) -> Iterator[Optional[DataSymbol]]:
        if not isinstance(self.obj, (list, tuple)):  # pragma: no cover
            raise TypeError(
                "tried to iterate through non-sequence namespace %s: %s", self, self.obj
            )
        # do the validation before starting the generator part so that we raise immediately
        return self._iter_inner()

    def _items_inner(self) -> Generator[Tuple[Any, Optional[DataSymbol]], None, None]:
        for key in self.obj.keys():
            yield key, self.lookup_data_symbol_by_name_this_indentation(
                key, is_subscript=True
            )

    def items(self) -> Iterator[Tuple[Any, Optional[DataSymbol]]]:
        if not isinstance(self.obj, dict):  # pragma: no cover
            raise TypeError(
                "tried to get iterate through items of non-dict namespace: %s", self.obj
            )
        # do the validation before starting the generator part so that we raise immediately
        return self._items_inner()

    @property
    def is_module(self):
        return isinstance(self.obj, ModuleType)

    @property
    def obj_id(self) -> int:
        return self.cached_obj_id

    @property
    def is_anonymous(self) -> bool:
        if self.scope_name == Namespace.ANONYMOUS:
            return True
        containing_ns = self.namespace_parent_scope
        if containing_ns is not None and containing_ns.is_anonymous:
            return True
        else:
            return False

    @property
    def is_garbage(self) -> bool:
        return self._tombstone

    def mark_garbage(self) -> None:
        if self.is_garbage:
            return
        self._tombstone = True
        for sym in self.all_data_symbols_this_indentation(exclude_class=True):
            sym.mark_garbage()

    def unmark_garbage(self) -> None:
        self._tombstone = False

    def collect_self_garbage(self) -> None:
        assert self.is_garbage
        assert (
            len(list(self.all_data_symbols_this_indentation(exclude_class=True))) == 0
        )
        flow().namespaces.pop(self.obj_id, None)

    @property
    def is_subscript(self) -> bool:
        dsym = flow().get_first_full_symbol(self.obj_id)
        if dsym is None:
            return False
        else:
            return dsym.is_subscript

    def max_cascading_reactive_cell_num(self, seen: Set[DataSymbol]) -> int:
        return max(
            (
                dsym.cascading_reactive_cell_num(
                    seen=seen, consider_containing_symbols=False
                )
                for dsym in self.all_data_symbols_this_indentation()
            ),
            default=-1,
        )

    def update_obj_ref(self, obj) -> None:
        self._tombstone = False
        flow().namespaces.pop(self.cached_obj_id, None)
        self.obj = obj
        self.cached_obj_id = id(obj)
        flow().namespaces[self.cached_obj_id] = self

    def data_symbol_by_name(
        self, is_subscript=False
    ) -> Dict[SupportedIndexType, DataSymbol]:
        if is_subscript:
            return self._subscript_data_symbol_by_name
        else:
            return self._data_symbol_by_name

    def clone(self, obj: Any) -> "Namespace":
        cloned = Namespace(obj, self.scope_name, self.parent_scope)
        cloned.cloned_from = self
        self.child_clones.append(cloned)
        return cloned

    @classmethod
    def make_child_namespace(cls, scope, scope_name) -> "Namespace":
        return cls(cls.PENDING_CLASS_PLACEHOLDER, scope_name, parent_scope=scope)

    def fresh_copy(self, obj: Any) -> "Namespace":
        return Namespace(obj, self.scope_name, self.parent_scope)

    def make_namespace_qualified_name(self, dsym: DataSymbol) -> str:
        path = self.full_namespace_path
        name = str(dsym.name)
        if path:
            if dsym.is_subscript or name.isdecimal():
                return f"{path}[{name}]"
            else:
                return f"{path}.{name}"
        else:
            return name

    def _lookup_subscript(self, name: SupportedIndexType) -> Optional[DataSymbol]:
        ret = self._subscript_data_symbol_by_name.get(name, None)
        if (
            isinstance(self.obj, Sequence)
            and isinstance(name, int)
            and hasattr(self.obj, "__len__")
        ):
            if name < 0 and ret is None:
                name = len(self.obj) + name
                ret = self._subscript_data_symbol_by_name.get(name, None)
        return ret

    def lookup_data_symbol_by_name_this_indentation(
        self,
        name: SupportedIndexType,
        *_,
        is_subscript: Optional[bool] = None,
        skip_cloned_lookup: bool = False,
        **kwargs: Any,
    ) -> Optional[DataSymbol]:
        if is_subscript is None:
            ret = self._data_symbol_by_name.get(name, None)
            if ret is None:
                ret = self._lookup_subscript(name)
        elif is_subscript:
            ret = self._lookup_subscript(name)
        else:
            ret = self._data_symbol_by_name.get(name, None)
        if (
            not skip_cloned_lookup
            and ret is None
            and self.cloned_from is not None
            and not is_subscript
            and isinstance(name, str)
        ):
            if name not in getattr(self.obj, "__dict__", {}):
                # only fall back to the class sym if it's not present in the corresponding obj for this scope
                ret = self.cloned_from.lookup_data_symbol_by_name_this_indentation(
                    name, is_subscript=is_subscript, **kwargs
                )
        return ret

    def _remap_sym(self, from_idx: int, to_idx: int, prev_obj: Optional[Any]) -> None:
        subsym = self._subscript_data_symbol_by_name.pop(from_idx, None)
        if subsym is None:
            return
        subsym.update_usage_info()
        subsym.name = to_idx
        subsym.invalidate_cached()  # ensure we bypass equality check and bump timestamp
        subsym.update_deps(
            set(),
            prev_obj,
            overwrite=False,
            propagate=True,
            refresh=True,
        )
        self._subscript_data_symbol_by_name[to_idx] = subsym

    def shuffle_symbols_upward_from(self, pos: int) -> None:
        for idx in range(len(self.obj) - 1, pos, -1):
            prev_obj = self.obj[idx + 1] if idx < len(self.obj) - 1 else None
            self._remap_sym(idx - 1, idx, prev_obj)

    def _shuffle_symbols_downward_to(self, pos: int) -> None:
        for idx in range(pos + 1, len(self.obj) + 1):
            prev_obj = self.obj[idx - 2] if idx > pos + 1 else None
            self._remap_sym(idx, idx - 1, prev_obj)

    def delete_data_symbol_for_name(
        self, name: SupportedIndexType, is_subscript: bool = False
    ) -> None:
        if is_subscript:
            dsym = self._subscript_data_symbol_by_name.pop(name, None)
            if dsym is None and name == -1 and isinstance(self.obj, list):
                name = len(
                    self.obj
                )  # it will have already been deleted, so don't subtract 1
                dsym = self._subscript_data_symbol_by_name.pop(name, None)
            if dsym is not None:
                dsym.update_deps(set(), deleted=True)
            if isinstance(self.obj, list) and isinstance(name, int):
                self._shuffle_symbols_downward_to(name)
        else:
            super().delete_data_symbol_for_name(name)

    def all_data_symbols_this_indentation(
        self, exclude_class=False, is_subscript=None
    ) -> Iterable[DataSymbol]:
        if is_subscript is None:
            dsym_collections_to_chain: List[Iterable] = [
                self._data_symbol_by_name.values(),
                self._subscript_data_symbol_by_name.values(),
            ]
        elif is_subscript:
            dsym_collections_to_chain = [self._subscript_data_symbol_by_name.values()]
        else:
            dsym_collections_to_chain = [self._data_symbol_by_name.values()]
        if self.cloned_from is not None and not exclude_class:
            dsym_collections_to_chain.append(
                self.cloned_from.all_data_symbols_this_indentation()
            )
        return itertools.chain(*dsym_collections_to_chain)

    def put(self, name: SupportedIndexType, val: DataSymbol) -> None:
        if val.is_subscript:
            self._subscript_data_symbol_by_name[name] = val
        elif not isinstance(name, str):  # pragma: no cover
            raise TypeError("%s should be a string" % name)
        else:
            self._data_symbol_by_name[name] = val
        val.containing_scope = self

    def refresh(self) -> None:
        self.max_descendent_timestamp = Timestamp.current()

    def get_earliest_ancestor_containing(
        self, obj_id: int, is_subscript: bool
    ) -> Optional["Namespace"]:
        # TODO: test this properly
        ret = None
        if self.namespace_parent_scope is not None:
            ret = self.namespace_parent_scope.get_earliest_ancestor_containing(
                obj_id, is_subscript
            )
        if ret is not None:
            return ret
        if obj_id in (
            dsym.obj_id
            for dsym in self.all_data_symbols_this_indentation(
                is_subscript=is_subscript
            )
        ):
            return self
        else:
            return None

    @property
    def namespace_parent_scope(self) -> Optional["Namespace"]:
        if self.parent_scope is not None and isinstance(self.parent_scope, Namespace):
            return self.parent_scope
        return None

    def iter_containing_namespaces(self) -> Generator["Namespace", None, None]:
        containing_ns = self
        while containing_ns is not None and containing_ns.is_namespace_scope:
            yield containing_ns
            containing_ns = containing_ns.parent_scope  # type: ignore

    def transfer_symbols_to(self, new_ns: "Namespace") -> None:
        for dsym in list(
            self.all_data_symbols_this_indentation(
                exclude_class=True, is_subscript=False
            )
        ):
            try:
                inner_obj = flow().retrieve_namespace_attr_or_sub(
                    new_ns.obj, dsym.name, is_subscript=False
                )
            except AttributeError:
                inner_obj = None
            except TypeError:
                break
            dsym.update_obj_ref(inner_obj)
            logger.info("shuffle %s from %s to %s", dsym, self, new_ns)
            self._data_symbol_by_name.pop(dsym.name, None)
            new_ns._data_symbol_by_name[dsym.name] = dsym
            dsym.containing_scope = new_ns
        for dsym in list(
            self.all_data_symbols_this_indentation(
                exclude_class=True, is_subscript=True
            )
        ):
            try:
                inner_obj = flow().retrieve_namespace_attr_or_sub(
                    new_ns.obj, dsym.name, is_subscript=True
                )
            except (IndexError, KeyError):
                inner_obj = None
            except TypeError:
                break
            dsym.update_obj_ref(inner_obj)
            logger.info("shuffle %s from %s to %s", dsym, self, new_ns)
            self._subscript_data_symbol_by_name.pop(dsym.name, None)
            new_ns._subscript_data_symbol_by_name[dsym.name] = dsym
            dsym.containing_scope = new_ns
