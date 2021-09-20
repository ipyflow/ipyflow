# -*- coding: future_annotations -*-
import ast
import inspect
import logging
from typing import cast, TYPE_CHECKING

from IPython import get_ipython

from nbsafety.analysis.attr_symbols import AttrSubSymbolChain, CallPoint
from nbsafety.data_model.data_symbol import DataSymbol, DataSymbolType
from nbsafety.utils.misc_utils import GetterFallback
from nbsafety.singletons import nbs

if TYPE_CHECKING:
    from typing import Any, Dict, Generator, Iterable, List, Mapping, Optional, Set, Tuple, Union
    from nbsafety.data_model.namespace import Namespace
    from nbsafety.types import SupportedIndexType


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


def _try_get_pandas():
    pandas = None
    try:
        import pandas
    except ImportError:
        pass
    return pandas


class Scope:
    GLOBAL_SCOPE_NAME = '<module>'

    def __init__(
        self,
        scope_name: str = GLOBAL_SCOPE_NAME,
        parent_scope: Optional[Scope] = None,
    ):
        self.scope_name = str(scope_name)
        self.parent_scope = parent_scope  # None iff this is the global scope
        self._data_symbol_by_name: Dict[SupportedIndexType, DataSymbol] = {}

    def __hash__(self):
        return hash(self.full_path)

    def __str__(self):
        return str(self.full_path)

    def __repr__(self):
        return str(self)

    def data_symbol_by_name(self, is_subscript=False):
        if is_subscript:
            raise ValueError('Only namespace scopes carry subscripts')
        return self._data_symbol_by_name

    @property
    def non_namespace_parent_scope(self) -> Optional[Scope]:
        # a scope nested inside of a namespace scope does not have access
        # to unqualified members of the namespace scope
        if self.is_global:
            return None
        if self.parent_scope.is_namespace_scope:
            return self.parent_scope.non_namespace_parent_scope
        return self.parent_scope

    def make_child_scope(self, scope_name) -> Scope:
        return Scope(scope_name, parent_scope=self)

    def put(self, name: SupportedIndexType, val: DataSymbol) -> None:
        self._data_symbol_by_name[name] = val
        val.containing_scope = self

    def lookup_data_symbol_by_name_this_indentation(self, name: SupportedIndexType, **_: Any) -> Optional[DataSymbol]:
        return self._data_symbol_by_name.get(name, None)

    def all_data_symbols_this_indentation(self):
        return self._data_symbol_by_name.values()

    def lookup_data_symbol_by_name(self, name: SupportedIndexType, **kwargs: Any) -> Optional[DataSymbol]:
        ret = self.lookup_data_symbol_by_name_this_indentation(name, **kwargs)
        if ret is None and self.non_namespace_parent_scope is not None:
            ret = self.non_namespace_parent_scope.lookup_data_symbol_by_name(name, **kwargs)
        return ret

    @staticmethod
    def _get_name_to_obj_mapping(obj: Any, dsym: DataSymbol) -> Mapping[SupportedIndexType, Any]:
        if obj is None:
            return get_ipython().ns_table['user_global']
        elif dsym is not None and dsym.is_subscript:
            return obj
        else:
            try:
                pandas = _try_get_pandas()
                if (pandas is not None) and isinstance(obj, pandas.DataFrame):
                    # FIXME: hack to get it working w/ pandas, which doesn't play nicely w/ inspect.getmembers
                    name_to_obj = {}
                    for col in obj.columns:
                        try:
                            name_to_obj[col] = getattr(obj, col)
                        except:
                            continue
                else:
                    name_to_obj = GetterFallback([obj.__dict__, getattr(type(obj), '__dict__', {})])  # type: ignore
            except:  # noqa
                return dict(inspect.getmembers(obj))
        return name_to_obj

    def gen_data_symbols_for_attrsub_chain(
        self, chain: AttrSubSymbolChain
    ) -> Generator[Tuple[DataSymbol, Optional[Union[SupportedIndexType, CallPoint]], bool, bool], None, None]:
        """
        Generates progressive symbols appearing in an AttrSub chain until
        this can no longer be done semi-statically (e.g. because one of the
        chain members is a CallPoint).
        """
        cur_scope = self
        obj = None
        for i, name in enumerate(chain.symbols):
            is_last = i == len(chain.symbols) - 1
            if isinstance(name, CallPoint):
                next_dsym = cur_scope.lookup_data_symbol_by_name(name.symbol)
                if next_dsym is not None:
                    yield next_dsym, None if is_last else chain.symbols[i + 1], True, False
                break
            next_dsym = cur_scope.lookup_data_symbol_by_name(name)
            if next_dsym is None:
                break
            else:
                try:
                    obj = self._get_name_to_obj_mapping(obj, next_dsym)[name]
                    yield next_dsym, None if is_last else chain.symbols[i + 1], False, is_last
                except (KeyError, IndexError, Exception):
                    break
            cur_scope = nbs().namespaces.get(id(obj), None)
            if cur_scope is None:
                break

    def get_most_specific_data_symbol_for_attrsub_chain(
        self, chain: AttrSubSymbolChain
    ) -> Optional[Tuple[DataSymbol, Optional[Union[SupportedIndexType, CallPoint]], bool, bool]]:
        """
        Get most specific DataSymbol for the whole chain (stops at first point it cannot find nested, e.g. a CallPoint).
        """
        ret = None
        for dsym, next_ref, is_called, success in self.gen_data_symbols_for_attrsub_chain(chain):
            ret = dsym, next_ref, is_called, success
        return ret

    @staticmethod
    def _resolve_symbol_type(
        overwrite: bool = True,
        is_subscript: bool = False,
        is_function_def: bool = False,
        is_import: bool = False,
        is_anonymous: bool = False,
        class_scope: Optional[Scope] = None,
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
        deps: Iterable[DataSymbol],
        stmt_node: ast.AST,
        overwrite: bool = True,
        is_subscript: bool = False,
        is_function_def: bool = False,
        is_import: bool = False,
        is_anonymous: bool = False,
        class_scope: Optional[Scope] = None,
        symbol_type: Optional[DataSymbolType] = None,
        propagate: bool = True,
        implicit: bool = False,
    ) -> DataSymbol:
        symbol_type = symbol_type or self._resolve_symbol_type(
            overwrite=overwrite,
            is_subscript=is_subscript,
            is_function_def=is_function_def,
            is_import=is_import,
            is_anonymous=is_anonymous,
            class_scope=class_scope
        )
        deps = set(deps)  # make a copy since we mutate it (see below fixme)
        dsym, prev_dsym, prev_obj = self._upsert_data_symbol_for_name_inner(
            name,
            obj,
            deps,  # FIXME: this updates deps, which is super super hacky
            symbol_type,
            stmt_node,
            implicit=implicit,
        )
        dsym.update_deps(
            deps,
            prev_obj=prev_obj,
            overwrite=overwrite,
            propagate=propagate,
            refresh=not implicit,
        )
        return dsym

    def _upsert_data_symbol_for_name_inner(
        self,
        name: SupportedIndexType,
        obj: Any,
        deps: Set[DataSymbol],
        symbol_type: DataSymbolType,
        stmt_node: ast.AST,
        implicit: bool = False,
    ) -> Tuple[DataSymbol, Optional[DataSymbol], Optional[Any]]:
        prev_obj = None
        prev_dsym = self.lookup_data_symbol_by_name_this_indentation(
            name, is_subscript=symbol_type == DataSymbolType.SUBSCRIPT, skip_cloned_lookup=True,
        )
        if implicit and symbol_type != DataSymbolType.ANONYMOUS:
            assert prev_dsym is None, 'expected None, got %s' % prev_dsym
        if prev_dsym is not None and self.is_globally_accessible:
            prev_obj = DataSymbol.NULL if prev_dsym.obj is None else prev_dsym.obj
            # TODO: handle case where new dc is of different type
            if name in self.data_symbol_by_name(prev_dsym.is_subscript) and prev_dsym.symbol_type == symbol_type:
                prev_dsym.update_obj_ref(obj, refresh_cached=False)
                # old_dsym.update_type(symbol_type)
                # if we're updating a pre-existing one, it should not be an implicit upsert
                assert stmt_node is not None
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
        if ns_self is not None and symbol_type == DataSymbolType.DEFAULT and ns_self.cloned_from is not None:
            # add the cloned symbol as a dependency of the symbol about to be created
            new_dep = ns_self.cloned_from.lookup_data_symbol_by_name_this_indentation(name, is_subscript=False)
            if new_dep is not None:
                deps.add(new_dep)
        dsym = DataSymbol(
            name, symbol_type, obj, self, stmt_node=stmt_node, refresh_cached_obj=False, implicit=implicit
        )
        self.put(name, dsym)
        return dsym, prev_dsym, prev_obj

    def delete_data_symbol_for_name(self, name: SupportedIndexType, is_subscript: bool = False):
        assert not is_subscript
        dsym = self._data_symbol_by_name.pop(name, None)
        if dsym is not None:
            dsym.update_deps(set(), deleted=True)

    @property
    def is_global(self):
        return self.parent_scope is None

    @property
    def is_globally_accessible(self):
        return self.is_global or (self.is_namespace_scope and self.parent_scope.is_globally_accessible)

    @property
    def is_namespace_scope(self):
        return False

    @property
    def namespace(self) -> Optional[Namespace]:
        if self.is_namespace_scope:
            return cast('Namespace', self)
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
            return ''
        if self.parent_scope is not None:
            prefix = self.parent_scope.full_namespace_path
        else:
            prefix = ''
        if prefix:
            if getattr(self, 'is_subscript', False):
                return f'{prefix}[{self.scope_name}]'
            else:
                return f'{prefix}.{self.scope_name}'
        else:
            return self.scope_name

    def make_namespace_qualified_name(self, dsym: DataSymbol) -> str:
        return str(dsym.name)
