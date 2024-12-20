# -*- coding: utf-8 -*-
import ast
import itertools
import logging
import symtable
from types import ModuleType
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Generator,
    Iterable,
    Optional,
    Set,
    Tuple,
    Union,
    cast,
)

import pyccolo as pyc

from ipyflow.analysis.live_refs import compute_live_dead_symbol_refs
from ipyflow.analysis.symbol_ref import Atom, SymbolRef
from ipyflow.data_model.symbol import Symbol, SymbolType
from ipyflow.models import _ScopeContainer, cells, scopes
from ipyflow.singletons import tracer, tracer_initialized
from ipyflow.types import SupportedIndexType

if TYPE_CHECKING:
    from ipyflow.data_model.namespace import Namespace


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


# just want to get rid of unused warning
_override_unused_warning_scopes = scopes


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
        self._symbol_by_name: Dict[SupportedIndexType, Symbol] = {}

    def __hash__(self):
        return hash(id(self))

    def __str__(self):
        return str(self.full_path)

    def __repr__(self):
        return str(self)

    def __getitem__(self, item: SupportedIndexType) -> Symbol:
        ret = self.get(item)
        if ret is None:
            raise KeyError("item not found: %s" % item)
        return ret

    def __contains__(self, item: SupportedIndexType) -> bool:
        return self.get(item) is not None

    def get(self, item: SupportedIndexType) -> Optional[Symbol]:
        return self.lookup_symbol_by_name_this_indentation(item)

    def symbol_by_name(self, is_subscript=False):
        if is_subscript:
            raise ValueError("Only namespace scopes carry subscripts")
        return self._symbol_by_name

    @property
    def non_namespace_parent_scope(self) -> Optional["Scope"]:
        # a scope nested inside of a namespace scope does not have access
        # to unqualified members of the namespace scope
        if self.is_global:
            return None
        if self.parent_scope.is_namespace_scope:  # type: ignore[union-attr]
            return self.parent_scope.non_namespace_parent_scope  # type: ignore[union-attr]
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

    def put(self, name: SupportedIndexType, val: Symbol) -> None:
        self._symbol_by_name[name] = val
        val.containing_scope = self

    def lookup_symbol_by_name_this_indentation(
        self, name: SupportedIndexType, **_: Any
    ) -> Optional[Symbol]:
        return self._symbol_by_name.get(name)

    def all_symbols_this_indentation(self) -> Iterable[Symbol]:
        return self._symbol_by_name.values()

    def lookup_symbol_by_name(
        self, name: SupportedIndexType, **kwargs: Any
    ) -> Optional[Symbol]:
        ret = self.lookup_symbol_by_name_this_indentation(name, **kwargs)
        if ret is None and self.non_namespace_parent_scope is not None:
            ret = self.non_namespace_parent_scope.lookup_symbol_by_name(name, **kwargs)
        return ret

    def lookup_symbol_by_qualified_name(self, qualified_name: str) -> Optional[Symbol]:
        scope_or_sym: Union["Scope", Symbol, None] = self
        for part in qualified_name.split("."):
            if isinstance(scope_or_sym, Symbol):
                scope_or_sym = scope_or_sym.namespace
            if not isinstance(scope_or_sym, Scope):
                return None
            scope_or_sym = scope_or_sym.lookup_symbol_by_name_this_indentation(
                part, is_subscript=False
            )
            if not isinstance(scope_or_sym, Symbol):
                return None
        return scope_or_sym if isinstance(scope_or_sym, Symbol) else None

    def gen_symbols_for_attrsub_chain(
        self, symbol_ref: SymbolRef
    ) -> Generator[Tuple[Symbol, Atom, Optional[Atom]], None, None]:
        """
        Generates progressive symbols appearing in an AttrSub chain until
        this can no longer be done semi-statically (e.g. because one of the
        chain members is a CallPoint).
        """
        cur_scope = self
        for i, atom in enumerate(symbol_ref.chain):
            is_last = i == len(symbol_ref.chain) - 1
            if atom.is_callpoint:
                next_sym = cur_scope.lookup_symbol_by_name(atom.value)
                if next_sym is not None:
                    yield next_sym, atom, None if is_last else symbol_ref.chain[i + 1]
                break
            next_sym = cur_scope.lookup_symbol_by_name(atom.value)
            if next_sym is None:
                break
            else:
                yield next_sym, atom, None if is_last else symbol_ref.chain[i + 1]
            ns = next_sym.namespace
            if ns is None:
                break
            else:
                cur_scope = ns

    def get_most_specific_symbol_for_attrsub_chain(
        self, chain: SymbolRef
    ) -> Optional[Tuple[Symbol, Atom, Optional[Atom]]]:
        """
        Get most specific Symbol for the whole chain (stops at first point it cannot find nested, e.g. a CallPoint).
        """
        ret = None
        for sym, atom, next_atom in self.gen_symbols_for_attrsub_chain(chain):
            ret = sym, atom, next_atom
        return ret

    def try_fully_resolve_attrsub_chain(self, chain: SymbolRef) -> Optional[Symbol]:
        sym, next_atom = None, None
        for sym, _, next_atom in self.gen_symbols_for_attrsub_chain(chain):
            pass
        return sym if next_atom is None else None

    @staticmethod
    def _resolve_symbol_type(
        obj: Any,
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
            return SymbolType.FUNCTION
        elif is_import:
            assert overwrite
            assert not is_subscript
            return SymbolType.IMPORT
        elif is_module:
            assert overwrite
            assert not is_subscript
            return SymbolType.MODULE
        elif class_scope is not None:
            assert overwrite
            assert not is_subscript
            return SymbolType.CLASS
        elif is_subscript:
            return SymbolType.SUBSCRIPT
        elif is_anonymous:
            return SymbolType.ANONYMOUS
        elif isinstance(obj, ModuleType):
            return SymbolType.MODULE
        else:
            return SymbolType.DEFAULT

    def _compute_is_static_write_for_assign(self, sym: Symbol) -> bool:
        if sym.symbol_node is None or sym.stmt_node is None:
            return False
        try:
            return (
                SymbolRef(sym.symbol_node, scope=self).canonical()
                in compute_live_dead_symbol_refs(sym.stmt_node, self)[1]
            )
        except TypeError:
            return False

    def _compute_is_static_write_for_def(self, sym: Symbol) -> bool:
        if sym.stmt_node is None:
            return False
        dead = compute_live_dead_symbol_refs(sym.stmt_node, self)[1]
        return isinstance(sym.name, str) and SymbolRef.from_string(sym.name) in dead

    def _compute_is_static_write_for_import(self, sym: Symbol) -> bool:
        assert isinstance(sym.stmt_node, (ast.Import, ast.ImportFrom))
        dead = compute_live_dead_symbol_refs(sym.stmt_node, self)[1]
        for import_name in sym.stmt_node.names:
            if (
                import_name.name == "*"
                or SymbolRef.from_string(import_name.asname or import_name.name)
                not in dead
            ):
                return False
        return True

    def _compute_is_static_write(self, sym: Symbol) -> bool:
        scope = self
        while scope.is_namespace_scope:
            scope = scope.parent_scope  # type: ignore[assignment]
        if not scope.is_global or sym.stmt_node is None:
            return False
        elif not pyc.is_outer_stmt(id(sym.stmt_node)):
            return False
        elif isinstance(sym.stmt_node, ast.Assign):
            # TODO: must be module level to be a static write
            return scope._compute_is_static_write_for_assign(sym)
        elif isinstance(
            sym.stmt_node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef)
        ):
            return scope._compute_is_static_write_for_def(sym)
        elif isinstance(sym.stmt_node, (ast.Import, ast.ImportFrom)):
            return scope._compute_is_static_write_for_import(sym)
        else:
            return False

    def upsert_symbol_for_name(
        self,
        name: SupportedIndexType,
        obj: Any,
        deps: Optional[Iterable[Symbol]] = None,
        stmt_node: Optional[Union[ast.stmt, ast.Lambda]] = None,
        symbol_node: Optional[ast.AST] = None,
        overwrite: bool = True,
        is_subscript: bool = False,
        is_function_def: bool = False,
        is_import: bool = False,
        is_module: bool = False,
        is_anonymous: bool = False,
        class_scope: Optional["Scope"] = None,
        symbol_type: Optional[SymbolType] = None,
        propagate: bool = True,
        implicit: bool = False,
        is_cascading_reactive: Optional[bool] = None,
    ) -> Symbol:
        symbol_type = symbol_type or self._resolve_symbol_type(
            obj=obj,
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
        sym, prev_sym, prev_obj = self._upsert_symbol_for_name_inner(
            name,
            obj,
            deps,  # FIXME: this updates deps, which is super super hacky
            symbol_type,
            stmt_node,
            symbol_node=symbol_node,
            implicit=implicit,
        )
        sym.update_deps(
            deps,
            prev_obj=prev_obj,
            overwrite=overwrite,
            propagate=propagate,
            refresh=not implicit,
            is_cascading_reactive=is_cascading_reactive,
        )
        if tracer_initialized():
            tracer().this_stmt_updated_symbols.add(sym)
        if cells().exec_counter() <= 0:
            return sym
        try:
            is_static_write = self._compute_is_static_write(sym)
        except SyntaxError:
            is_static_write = False
        current_cell = cells().current_cell()
        for subsym in itertools.chain([sym], sym.get_namespace_symbols(recurse=True)):
            if is_static_write and subsym not in current_cell.dynamic_writes:
                current_cell._pending_dynamic_writes.discard(subsym)
                current_cell.static_writes.add(subsym)
            else:
                current_cell.static_writes.discard(sym)
                current_cell._pending_dynamic_writes.add(sym)
        return sym

    def _upsert_symbol_for_name_inner(
        self,
        name: SupportedIndexType,
        obj: Any,
        deps: Set[Symbol],
        symbol_type: SymbolType,
        stmt_node: Optional[Union[ast.stmt, ast.Lambda]] = None,
        symbol_node: Optional[ast.AST] = None,
        implicit: bool = False,
    ) -> Tuple[Symbol, Optional[Symbol], Optional[Any]]:
        prev_obj = None
        prev_sym = self.lookup_symbol_by_name_this_indentation(
            name,
            is_subscript=symbol_type == SymbolType.SUBSCRIPT,
            skip_cloned_lookup=True,
        )
        if prev_sym is not None:
            prev_obj = Symbol.NULL if prev_sym.obj is None else prev_sym.obj
            # TODO: handle case where new sym is of different type
            if (
                name in self.symbol_by_name(prev_sym.is_subscript)
                and prev_sym.symbol_type == symbol_type
            ):
                prev_sym.update_obj_ref(obj, refresh_cached=False)
                # old_sym.update_type(symbol_type)
                prev_sym.update_stmt_node(stmt_node)
                prev_sym.symbol_node = symbol_node
                return prev_sym, prev_sym, prev_obj
            else:
                # In this case, we are copying from a class and we need the sym from which we are copying
                # as able to propagate to the new sym.
                # Example:
                # class Foo:
                #     shared = 99
                # foo = Foo()
                # foo.shared = 42  # old_sym refers to Foo.shared here
                # Earlier, we were explicitly adding Foo.shared as a dependency of foo.shared as follows:
                # deps.add(old_sym)
                # But it turns out not to be necessary because foo depends on Foo, and changing Foo.shared will
                # propagate up the namespace hierarchy to Foo, which propagates to foo, which then propagates to
                # all of foo's namespace children (e.g. foo.shared).
                # This raises the question of whether we should draw the foo <-> Foo edge, since irrelevant namespace
                # children could then also be affected (e.g. some instance variable foo.x).
                # Perhaps a better strategy is to prevent propagation along this edge unless class Foo is redeclared.
                # If we do this, then we should go back to explicitly adding the dep as follows:
                # EDIT: added check to avoid propagating along class -> instance edge when class not redefined, so now
                # it is important to explicitly add this dep.
                deps.add(prev_sym)
        ns_self = self.namespace
        if (
            ns_self is not None
            and symbol_type == SymbolType.DEFAULT
            and ns_self.cloned_from is not None
        ):
            # add the cloned symbol as a dependency of the symbol about to be created
            new_dep = ns_self.cloned_from.lookup_symbol_by_name_this_indentation(
                name, is_subscript=False
            )
            if new_dep is not None:
                deps.add(new_dep)
        sym = Symbol(
            name,
            symbol_type,
            obj,
            self,
            stmt_node=stmt_node,
            symbol_node=symbol_node,
            refresh_cached_obj=False,
            implicit=implicit,
        )
        self.put(name, sym)
        return sym, prev_sym, prev_obj

    def delete_symbol_for_name(
        self, name: SupportedIndexType, is_subscript: bool = False
    ):
        assert not is_subscript
        sym = self._symbol_by_name.pop(name, None)
        if sym is not None:
            sym.update_deps(set(), deleted=True)
            sym.mark_garbage()

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
            return self.parent_scope.full_path + path  # type: ignore[union-attr]

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

    def make_namespace_qualified_name(self, sym: Symbol) -> str:
        return str(sym.name)


if len(_ScopeContainer) == 0:
    _ScopeContainer.append(Scope)
else:
    _ScopeContainer[0] = Scope
