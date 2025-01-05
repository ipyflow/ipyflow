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

from ipyflow.data_model.scope import Scope
from ipyflow.data_model.symbol import Symbol
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.models import _NamespaceContainer, namespaces
from ipyflow.singletons import flow
from ipyflow.types import SupportedIndexType

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


# just want to get rid of unused warning
_override_unused_warning_namespaces = namespaces


class Namespace(Scope):
    ANONYMOUS = "<anonymous_namespace>"

    PENDING_CLASS_PLACEHOLDER = object()

    # special object for virtually representing the file system
    FILE_SYSTEM: Dict[str, None] = dict()

    # TODO: support (multiple) inheritance by allowing
    #  Namespaces from classes to clone their parent class's Namespaces
    def __init__(
        self, obj: Any, *args, force_allow_iteration: bool = False, **kwargs
    ) -> None:
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
            if flow().is_dev_mode:
                raise ValueError(msg)
            else:
                logger.warning(msg)
        if obj is not self.PENDING_CLASS_PLACEHOLDER:
            flow().namespaces[id(obj)] = self
        self._tombstone = False
        # this timestamp needs to be bumped in Symbol refresh()
        self.max_descendent_timestamp: Timestamp = Timestamp.uninitialized()
        self._subscript_symbol_by_name: Dict[SupportedIndexType, Symbol] = {}
        self.namespace_waiting_symbols: Set[Symbol] = set()
        self._force_allow_iteration = force_allow_iteration

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
        return len(self._subscript_symbol_by_name) + len(self._symbol_by_name)

    def _iter_inner(self) -> Generator[Optional[Symbol], None, None]:
        if isinstance(self.obj, (list, tuple)):
            limit = len(self.obj)
        else:
            limit = len(self._subscript_symbol_by_name)
        for i in range(limit):
            yield self.lookup_symbol_by_name_this_indentation(i, is_subscript=True)

    def __iter__(self) -> Iterator[Optional[Symbol]]:
        if not self._force_allow_iteration and not isinstance(
            self.obj, (list, tuple)
        ):  # pragma: no cover
            raise TypeError(
                "tried to iterate through non-sequence namespace %s: %s", self, self.obj
            )
        # do the validation before starting the generator part so that we raise immediately
        return self._iter_inner()

    def _items_inner(self) -> Generator[Tuple[Any, Optional[Symbol]], None, None]:
        for key in self.obj.keys():
            yield key, self.lookup_symbol_by_name_this_indentation(
                key, is_subscript=True
            )

    def items(self) -> Iterator[Tuple[Any, Optional[Symbol]]]:
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
        for sym in self.all_symbols_this_indentation(exclude_class=True):
            sym.mark_garbage()

    def unmark_garbage(self) -> None:
        self._tombstone = False

    def collect_self_garbage(self) -> None:
        assert self.is_garbage
        assert len(list(self.all_symbols_this_indentation(exclude_class=True))) == 0
        flow().namespaces.pop(self.obj_id, None)

    @property
    def is_subscript(self) -> bool:
        sym = flow().get_first_full_symbol(self.obj_id)
        if sym is None:
            return False
        else:
            return sym.is_subscript

    def max_cascading_reactive_cell_num(self, seen: Set[Symbol]) -> int:
        return max(
            (
                sym.cascading_reactive_cell_num(
                    seen=seen, consider_containing_symbols=False
                )
                for sym in self.all_symbols_this_indentation()
            ),
            default=-1,
        )

    def update_obj_ref(self, obj) -> None:
        self._tombstone = False
        flow().namespaces.pop(self.cached_obj_id, None)
        self.obj = obj
        self.cached_obj_id = id(obj)
        flow().namespaces[self.cached_obj_id] = self

    def symbol_by_name(self, is_subscript=False) -> Dict[SupportedIndexType, Symbol]:
        if is_subscript:
            return self._subscript_symbol_by_name
        else:
            return self._symbol_by_name

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

    def make_namespace_qualified_name(self, sym: Symbol) -> str:
        path = self.full_namespace_path
        name = str(sym.name)
        if path:
            if sym.is_subscript or name.isdecimal():
                return f"{path}[{name}]"
            else:
                return f"{path}.{name}"
        else:
            return name

    def _lookup_subscript(self, name: SupportedIndexType) -> Optional[Symbol]:
        ret = self._subscript_symbol_by_name.get(name)
        if (
            isinstance(self.obj, Sequence)
            and isinstance(name, int)
            and hasattr(self.obj, "__len__")
        ):
            if name < 0 and ret is None:
                name = len(self.obj) + name
                ret = self._subscript_symbol_by_name.get(name)
        return ret

    def lookup_symbol_by_name_this_indentation(
        self,
        name: SupportedIndexType,
        *_,
        is_subscript: Optional[bool] = None,
        skip_cloned_lookup: bool = False,
        **kwargs: Any,
    ) -> Optional[Symbol]:
        if is_subscript is None:
            ret = self._symbol_by_name.get(name, None)
            if ret is None:
                ret = self._lookup_subscript(name)
        elif is_subscript:
            ret = self._lookup_subscript(name)
        else:
            ret = self._symbol_by_name.get(name, None)
        if (
            not skip_cloned_lookup
            and ret is None
            and self.cloned_from is not None
            and not is_subscript
            and isinstance(name, str)
        ):
            if name not in getattr(self.obj, "__dict__", {}):
                # only fall back to the class sym if it's not present in the corresponding obj for this scope
                ret = self.cloned_from.lookup_symbol_by_name_this_indentation(
                    name, is_subscript=is_subscript, **kwargs
                )
        return ret

    def lookup_symbol_by_name(
        self, name: SupportedIndexType, *args: Any, **kwargs: Any
    ) -> Optional[Symbol]:
        return self.lookup_symbol_by_name_this_indentation(name, *args, **kwargs)

    def _remap_sym(self, from_idx: int, to_idx: int, prev_obj: Optional[Any]) -> None:
        subsym = self._subscript_symbol_by_name.pop(from_idx, None)
        if subsym is None:
            return
        subsym.name = to_idx
        subsym.invalidate_cached()  # ensure we bypass equality check and bump timestamp
        subsym.update_deps(
            set(),
            prev_obj,
            overwrite=False,
            propagate=True,
            refresh=True,
        )
        self._subscript_symbol_by_name[to_idx] = subsym
        subsym._is_dangling_on_edges = True

    def shuffle_symbols_upward_from(self, pos: int) -> None:
        for idx in range(len(self.obj) - 1, pos, -1):
            prev_obj = self.obj[idx + 1] if idx < len(self.obj) - 1 else None
            self._remap_sym(idx - 1, idx, prev_obj)

    def _shuffle_symbols_downward_to(self, pos: int) -> None:
        for idx in range(pos + 1, len(self.obj) + 1):
            prev_obj = self.obj[idx - 2] if idx > pos + 1 else None
            self._remap_sym(idx, idx - 1, prev_obj)

    def delete_symbol_for_name(
        self, name: SupportedIndexType, is_subscript: bool = False
    ) -> None:
        if is_subscript:
            sym = self._subscript_symbol_by_name.pop(name, None)
            if sym is None and name == -1 and isinstance(self.obj, list):
                name = len(
                    self.obj
                )  # it will have already been deleted, so don't subtract 1
                sym = self._subscript_symbol_by_name.pop(name, None)
            if sym is not None:
                sym.update_deps(set(), deleted=True)
            if isinstance(self.obj, list) and isinstance(name, int):
                self._shuffle_symbols_downward_to(name)
        else:
            super().delete_symbol_for_name(name)

    def all_symbols_this_indentation(
        self, exclude_class=False, is_subscript=None
    ) -> Iterable[Symbol]:
        if is_subscript is None:
            sym_collections_to_chain: List[Iterable] = [
                self._symbol_by_name.values(),
                self._subscript_symbol_by_name.values(),
            ]
        elif is_subscript:
            sym_collections_to_chain = [self._subscript_symbol_by_name.values()]
        else:
            sym_collections_to_chain = [self._symbol_by_name.values()]
        if self.cloned_from is not None and not exclude_class:
            sym_collections_to_chain.append(
                self.cloned_from.all_symbols_this_indentation()
            )
        return itertools.chain(*sym_collections_to_chain)

    def put(self, name: SupportedIndexType, val: Symbol) -> None:
        if val.is_subscript:
            self._subscript_symbol_by_name[name] = val
        elif not isinstance(name, str):  # pragma: no cover
            raise TypeError("%s should be a string" % name)
        else:
            self._symbol_by_name[name] = val
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
            sym.obj_id
            for sym in self.all_symbols_this_indentation(is_subscript=is_subscript)
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
        for sym in list(
            self.all_symbols_this_indentation(exclude_class=True, is_subscript=False)
        ):
            try:
                inner_obj = flow().retrieve_namespace_attr_or_sub(
                    new_ns.obj, sym.name, is_subscript=False
                )
            except AttributeError:
                continue
            except TypeError:
                break
            sym.update_obj_ref(inner_obj)
            logger.info("shuffle %s from %s to %s", sym, self, new_ns)
            self._symbol_by_name.pop(sym.name, None)
            new_ns._symbol_by_name[sym.name] = sym
            sym.containing_scope = new_ns
        for sym in list(
            self.all_symbols_this_indentation(exclude_class=True, is_subscript=True)
        ):
            try:
                inner_obj = flow().retrieve_namespace_attr_or_sub(
                    new_ns.obj, sym.name, is_subscript=True
                )
            except (IndexError, KeyError):
                continue
            except TypeError:
                break
            sym.update_obj_ref(inner_obj)
            logger.info("shuffle %s from %s to %s", sym, self, new_ns)
            self._subscript_symbol_by_name.pop(sym.name, None)
            new_ns._subscript_symbol_by_name[sym.name] = sym
            sym.containing_scope = new_ns


if len(_NamespaceContainer) == 0:
    _NamespaceContainer.append(Namespace)
else:
    _NamespaceContainer[0] = Namespace
