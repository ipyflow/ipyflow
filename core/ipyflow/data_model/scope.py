# -*- coding: utf-8 -*-
import ast
import logging
import symtable
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Generator,
    Iterable,
    Optional,
    Set,
    Tuple,
    cast,
)

from ipyflow.analysis.symbol_ref import Atom, SymbolRef
from ipyflow.data_model.data_symbol import DataSymbol, DataSymbolType
from ipyflow.singletons import tracer
from ipyflow.types import SupportedIndexType

if TYPE_CHECKING:
    from ipyflow.data_model.namespace import Namespace


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class Scope:
    GLOBAL_SCOPE_NAME = "<module>"

    def __init__(
        self,
        scope_name: str = GLOBAL_SCOPE_NAME,
        parent_scope: Optional["Scope"] = None,
        symtab: Optional[symtable.SymbolTable] = None,
    ):
        self.scope_name = str(scope_name)
        self.parent_scope = parent_scope  # None iff this is the global scope
        self.symtab = symtab
        self._data_symbol_by_name: Dict[SupportedIndexType, DataSymbol] = {}

    def __hash__(self):
        return hash(id(self))

    def __str__(self):
        return str(self.full_path)

    def __repr__(self):
        return str(self)

    def data_symbol_by_name(self, is_subscript=False):
        if is_subscript:
            raise ValueError("Only namespace scopes carry subscripts")
        return self._data_symbol_by_name

    @property
    def non_namespace_parent_scope(self) -> Optional["Scope"]:
        # a scope nested inside of a namespace scope does not have access
        # to unqualified members of the namespace scope
        if self.is_global:
            return None
        if self.parent_scope.is_namespace_scope:
            return self.parent_scope.non_namespace_parent_scope
        return self.parent_scope

    def make_child_scope(self, scope_name) -> "Scope":
        symtab = tracer().cur_cell_symtab if self.is_global else self.symtab
        child_symtab = None
        if symtab is not None:
            try:
                sym = symtab.lookup(scope_name)
                if sym.is_namespace():
                    child_symtab = sym.get_namespace()
            except KeyError:
                pass
            except ValueError:
                pass
        return Scope(scope_name, parent_scope=self, symtab=child_symtab)

    def put(self, name: SupportedIndexType, val: DataSymbol) -> None:
        self._data_symbol_by_name[name] = val
        val.containing_scope = self

    def lookup_data_symbol_by_name_this_indentation(
        self, name: SupportedIndexType, **_: Any
    ) -> Optional[DataSymbol]:
        return self._data_symbol_by_name.get(name, None)

    def all_data_symbols_this_indentation(self):
        return self._data_symbol_by_name.values()

    def lookup_data_symbol_by_name(
        self, name: SupportedIndexType, **kwargs: Any
    ) -> Optional[DataSymbol]:
        ret = self.lookup_data_symbol_by_name_this_indentation(name, **kwargs)
        if ret is None and self.non_namespace_parent_scope is not None:
            ret = self.non_namespace_parent_scope.lookup_data_symbol_by_name(
                name, **kwargs
            )
        return ret

    def gen_data_symbols_for_attrsub_chain(
        self, symbol_ref: SymbolRef
    ) -> Generator[Tuple[DataSymbol, Atom, Optional[Atom]], None, None]:
        """
        Generates progressive symbols appearing in an AttrSub chain until
        this can no longer be done semi-statically (e.g. because one of the
        chain members is a CallPoint).
        """
        cur_scope = self
        for i, atom in enumerate(symbol_ref.chain):
            is_last = i == len(symbol_ref.chain) - 1
            if atom.is_callpoint:
                next_dsym = cur_scope.lookup_data_symbol_by_name(atom.value)
                if next_dsym is not None:
                    yield next_dsym, atom, None if is_last else symbol_ref.chain[i + 1]
                break
            next_dsym = cur_scope.lookup_data_symbol_by_name(atom.value)
            if next_dsym is None:
                break
            else:
                yield next_dsym, atom, None if is_last else symbol_ref.chain[i + 1]
            cur_scope = next_dsym.namespace
            if cur_scope is None:
                break

    def get_most_specific_data_symbol_for_attrsub_chain(
        self, chain: SymbolRef
    ) -> Optional[Tuple[DataSymbol, Atom, Optional[Atom]]]:
        """
        Get most specific DataSymbol for the whole chain (stops at first point it cannot find nested, e.g. a CallPoint).
        """
        ret = None
        for dsym, atom, next_atom in self.gen_data_symbols_for_attrsub_chain(chain):
            ret = dsym, atom, next_atom
        return ret

    @staticmethod
    def _resolve_symbol_type(
        overwrite: bool = True,
        is_subscript: bool = False,
        is_function_def: bool = False,
        is_import: bool = False,
        is_module: bool = False,
        is_anonymous: bool = False,
        class_scope: Optional["Scope"] = None,
    ):
        assert not (class_scope is not None and (is_function_def or is_import))
        if is_function_def:
            assert overwrite
            assert not is_subscript
            return DataSymbolType.FUNCTION
        elif is_import:
            assert overwrite
            assert not is_subscript
            return DataSymbolType.IMPORT
        elif is_module:
            assert overwrite
            assert not is_subscript
            return DataSymbolType.MODULE
        elif class_scope is not None:
            assert overwrite
            assert not is_subscript
            return DataSymbolType.CLASS
        elif is_subscript:
            return DataSymbolType.SUBSCRIPT
        elif is_anonymous:
            return DataSymbolType.ANONYMOUS
        else:
            return DataSymbolType.DEFAULT

    def upsert_data_symbol_for_name(
        self,
        name: SupportedIndexType,
        obj: Any,
        deps: Optional[Iterable[DataSymbol]] = None,
        stmt_node: Optional[ast.stmt] = None,
        symbol_node: Optional[ast.AST] = None,
        overwrite: bool = True,
        is_subscript: bool = False,
        is_function_def: bool = False,
        is_import: bool = False,
        is_module: bool = False,
        is_anonymous: bool = False,
        class_scope: Optional["Scope"] = None,
        symbol_type: Optional[DataSymbolType] = None,
        propagate: bool = True,
        implicit: bool = False,
        is_cascading_reactive: Optional[bool] = None,
    ) -> DataSymbol:
        symbol_type = symbol_type or self._resolve_symbol_type(
            overwrite=overwrite,
            is_subscript=is_subscript,
            is_function_def=is_function_def,
            is_import=is_import,
            is_module=is_module,
            is_anonymous=is_anonymous,
            class_scope=class_scope,
        )
        deps = set(
            [] if deps is None else deps
        )  # make a copy since we mutate it (see below fixme)
        dsym, prev_dsym, prev_obj = self._upsert_data_symbol_for_name_inner(
            name,
            obj,
            deps,  # FIXME: this updates deps, which is super super hacky
            symbol_type,
            stmt_node,
            symbol_node=symbol_node,
            implicit=implicit,
        )
        dsym.update_deps(
            deps,
            prev_obj=prev_obj,
            overwrite=overwrite,
            propagate=propagate,
            refresh=not implicit,
            is_cascading_reactive=is_cascading_reactive,
        )
        tracer().this_stmt_updated_symbols.add(dsym)
        return dsym

    def _upsert_data_symbol_for_name_inner(
        self,
        name: SupportedIndexType,
        obj: Any,
        deps: Set[DataSymbol],
        symbol_type: DataSymbolType,
        stmt_node: Optional[ast.stmt] = None,
        symbol_node: Optional[ast.AST] = None,
        implicit: bool = False,
    ) -> Tuple[DataSymbol, Optional[DataSymbol], Optional[Any]]:
        prev_obj = None
        prev_dsym = self.lookup_data_symbol_by_name_this_indentation(
            name,
            is_subscript=symbol_type == DataSymbolType.SUBSCRIPT,
            skip_cloned_lookup=True,
        )
        if prev_dsym is not None:
            prev_obj = DataSymbol.NULL if prev_dsym.obj is None else prev_dsym.obj
            # TODO: handle case where new dsym is of different type
            if (
                name in self.data_symbol_by_name(prev_dsym.is_subscript)
                and prev_dsym.symbol_type == symbol_type
            ):
                prev_dsym.update_obj_ref(obj, refresh_cached=False)
                # old_dsym.update_type(symbol_type)
                prev_dsym.update_stmt_node(stmt_node)
                return prev_dsym, prev_dsym, prev_obj
            else:
                # In this case, we are copying from a class and we need the dsym from which we are copying
                # as able to propagate to the new dsym.
                # Example:
                # class Foo:
                #     shared = 99
                # foo = Foo()
                # foo.shared = 42  # old_dsym refers to Foo.shared here
                # Earlier, we were explicitly adding Foo.shared as a dependency of foo.shared as follows:
                # deps.add(old_dsym)
                # But it turns out not to be necessary because foo depends on Foo, and changing Foo.shared will
                # propagate up the namespace hierarchy to Foo, which propagates to foo, which then propagates to
                # all of foo's namespace children (e.g. foo.shared).
                # This raises the question of whether we should draw the foo <-> Foo edge, since irrelevant namespace
                # children could then also be affected (e.g. some instance variable foo.x).
                # Perhaps a better strategy is to prevent propagation along this edge unless class Foo is redeclared.
                # If we do this, then we should go back to explicitly adding the dep as follows:
                # EDIT: added check to avoid propagating along class -> instance edge when class not redefined, so now
                # it is important to explicitly add this dep.
                deps.add(prev_dsym)
        ns_self = self.namespace
        if (
            ns_self is not None
            and symbol_type == DataSymbolType.DEFAULT
            and ns_self.cloned_from is not None
        ):
            # add the cloned symbol as a dependency of the symbol about to be created
            new_dep = ns_self.cloned_from.lookup_data_symbol_by_name_this_indentation(
                name, is_subscript=False
            )
            if new_dep is not None:
                deps.add(new_dep)
        dsym = DataSymbol(
            name,
            symbol_type,
            obj,
            self,
            stmt_node=stmt_node,
            symbol_node=symbol_node,
            refresh_cached_obj=False,
            implicit=implicit,
        )
        self.put(name, dsym)
        return dsym, prev_dsym, prev_obj

    def delete_data_symbol_for_name(
        self, name: SupportedIndexType, is_subscript: bool = False
    ):
        assert not is_subscript
        dsym = self._data_symbol_by_name.pop(name, None)
        if dsym is not None:
            dsym.update_deps(set(), deleted=True)
            dsym.mark_garbage()

    @property
    def is_global(self):
        return self.parent_scope is None

    @property
    def is_module(self):
        return False

    @property
    def is_globally_accessible(self):
        return self.is_global or (
            self.is_namespace_scope and self.parent_scope.is_globally_accessible
        )

    @property
    def is_namespace_scope(self):
        return False

    @property
    def namespace(self) -> Optional["Namespace"]:
        if self.is_namespace_scope:
            return cast("Namespace", self)
        else:
            return None

    @property
    def global_scope(self):
        if self.is_global:
            return self
        return self.parent_scope.global_scope

    @property
    def full_path(self) -> Tuple[str, ...]:
        path = (self.scope_name,)
        if self.is_global:
            return path
        else:
            return self.parent_scope.full_path + path

    @property
    def full_namespace_path(self) -> str:
        if not self.is_namespace_scope:
            return ""
        if self.parent_scope is not None:
            prefix = self.parent_scope.full_namespace_path
        else:
            prefix = ""
        if prefix:
            if self.scope_name.isdecimal() or getattr(self, "is_subscript", False):
                return f"{prefix}[{self.scope_name}]"
            else:
                return f"{prefix}.{self.scope_name}"
        else:
            return self.scope_name

    def make_namespace_qualified_name(self, dsym: DataSymbol) -> str:
        return str(dsym.name)
