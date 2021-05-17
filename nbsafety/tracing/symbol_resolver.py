# -*- coding: future_annotations -*-
import ast
import logging
from typing import List, TYPE_CHECKING

from nbsafety.analysis.attr_symbols import resolve_slice_to_constant
from nbsafety.analysis.mixins import SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.data_model.scope import NamespaceScope
from nbsafety.singletons import nbs, tracer

if TYPE_CHECKING:
    from typing import Dict, Optional, Set, Tuple, Union


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class ResolveRvalSymbols(SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self):
        self.symbols: List[Optional[DataSymbol]] = []

    def __call__(self, node: ast.AST) -> Set[DataSymbol]:
        self.visit(node)
        return {sym for sym in self.symbols if sym is not None}

    def _push_symbols(self):
        return self.push_attributes(symbols=[])

    def visit_Name(self, node: ast.Name):
        self.symbols.extend(tracer().resolve_loaded_symbols(node))

    def visit_Tuple(self, node: ast.Tuple):
        self.visit_List_or_Tuple(node)

    def visit_List(self, node: ast.List):
        self.visit_List_or_Tuple(node)

    def visit_Dict(self, node: ast.Dict):
        resolved = tracer().resolve_loaded_symbols(node)
        if not resolved:
            # if id(node) not in tracer().node_id_to_loaded_literal_scope:
            # only descend if tracer failed to create literal symbol
            self.generic_visit(node.keys)
            self.generic_visit(node.values)
        else:
            self.symbols.extend(resolved)

    def visit_List_or_Tuple(self, node: Union[ast.List, ast.Tuple]):
        resolved = tracer().resolve_loaded_symbols(node)
        if not resolved:
            # if id(node) not in tracer().node_id_to_loaded_literal_scope:
            # only descend if tracer failed to create literal symbol
            self.generic_visit(node.elts)
        else:
            self.symbols.extend(resolved)

    def visit_AugAssign_or_AnnAssign(self, node):
        self.visit(node.value)

    def visit_AnnAssign(self, node):
        self.visit_AugAssign_or_AnnAssign(node)

    def visit_AugAssign(self, node):
        self.visit_AugAssign_or_AnnAssign(node)

    def visit_Call(self, node):
        if isinstance(node.func, (ast.Attribute, ast.Subscript)):
            self.visit(node.func)
        self.symbols.extend(tracer().resolve_loaded_symbols(node.func))
        self.symbols.extend(tracer().resolve_loaded_symbols(node))
        self.generic_visit([node.args, node.keywords])

    def _get_attr_or_subscript_namespace(self, node: Union[ast.Attribute, ast.Subscript]) -> Optional[NamespaceScope]:
        with self._push_symbols():
            self.visit(node.value)
            symbols = self.symbols
        if len(symbols) != 1 or symbols[0] is None:
            return None
        return nbs().namespaces.get(symbols[0].obj_id, None)

    def visit_Attribute(self, node: ast.Attribute):
        if isinstance(node.value, ast.Call):
            self.visit(node.value)
        symbols = tracer().resolve_loaded_symbols(node)
        if len(symbols) > 0:
            self.symbols.extend(symbols)
            return
        # TODO: this path lacks coverage
        try:
            ns = self._get_attr_or_subscript_namespace(node)
            if ns is None:
                return
            dsym = ns.lookup_data_symbol_by_name_this_indentation(node.attr, is_subscript=False)
            if dsym is not None:
                self.symbols.append(dsym)
        except Exception as e:
            logger.warning("Exception occurred while resolving node %s: %s", ast.dump(node), e)

    def visit_Subscript(self, node: ast.Subscript):
        if isinstance(node.value, ast.Call):
            self.visit(node.value)
        symbols = tracer().resolve_loaded_symbols(node)
        with self._push_symbols():
            # add slice to RHS to avoid propagating to it
            self.visit(node.slice)
            symbols.extend(self.symbols)
        if len(symbols) > 0:
            self.symbols.extend(symbols)
            return
        # TODO: this path lacks coverage
        try:
            slice = resolve_slice_to_constant(node)
            if slice is None or isinstance(slice, ast.Name):
                return
            with self._push_symbols():
                self.visit(node.value)
                symbols = self.symbols
            if len(symbols) != 1 or symbols[0] is None:
                return
            ns = self._get_attr_or_subscript_namespace(node)
            if ns is None:
                return
            dsym = ns.lookup_data_symbol_by_name_this_indentation(slice, is_subscript=True)
            if dsym is None and isinstance(slice, int) and slice < 0:
                try:
                    dsym = ns.lookup_data_symbol_by_name_this_indentation(len(ns) + slice, is_subscript=True)
                except TypeError:
                    dsym = None
            if dsym is not None:
                self.symbols.append(dsym)
        except Exception as e:
            logger.warning("Exception occurred while resolving node %s: %s", ast.dump(node), e)

    def visit_keyword(self, node: ast.keyword):
        self.visit(node.value)

    def visit_Starred(self, node: ast.Starred):
        self.symbols.extend(tracer().resolve_loaded_symbols(node))

    def visit_Lambda(self, node):
        self.symbols.extend(tracer().resolve_loaded_symbols(node))

    def visit_GeneratorExp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_DictComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_ListComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_SetComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(self, node):
        to_append = set()
        for gen in node.generators:
            if isinstance(gen, ast.comprehension):
                with self._push_symbols():
                    self.visit(gen.iter)
                    self.visit(gen.ifs)
                    to_append |= set(self.symbols)
                with self._push_symbols():
                    self.visit(gen.target)
                    discard_set = set(self.symbols)
            else:
                with self._push_symbols():
                    self.visit(gen)
                    discard_set = set(self.symbols)
            to_append -= discard_set
        self.symbols.extend(to_append - discard_set)

    def visit_arg(self, node: ast.arg):
        self.symbols.extend(tracer().resolve_loaded_symbols(node.arg))

    def visit_For(self, node: ast.For):
        # skip body -- will have dummy since this visitor works line-by-line
        self.visit(node.iter)

    def visit_If(self, node: ast.If):
        # skip body here too
        self.visit(node.test)

    def visit_FunctionDef_or_AsyncFunctionDef(self, node: Union[ast.AsyncFunctionDef, ast.FunctionDef]):
        self.visit(node.args)
        self.generic_visit(node.decorator_list)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.visit_FunctionDef_or_AsyncFunctionDef(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.visit_FunctionDef_or_AsyncFunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        self.generic_visit(node.bases)
        self.generic_visit(node.decorator_list)

    def visit_With(self, node: ast.With):
        # skip body
        self.generic_visit(node.items)

    def visit_withitem(self, node: ast.withitem):
        self.visit(node.context_expr)

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        # important: this needs to skip the body
        if node.type is not None:
            self.symbols.extend(tracer().resolve_loaded_symbols(node.type))

    def visit_Import(self, node: ast.Import):
        pass

    def visit_ImportFrom(self, node: ast.ImportFrom):
        pass


def update_usage_info(symbols: Union[Optional[DataSymbol], Set[Optional[DataSymbol]]]):
    cell_counter = nbs().cell_counter()
    for sym in (symbols if symbols is not None and isinstance(symbols, set) else [symbols]):
        if sym is None:
            continue
        sym.last_used_cell_num = cell_counter
        logger.info('sym `%s` used in cell %d last updated in cell %d', sym, cell_counter, sym.timestamp)
        sym.timestamp_by_used_time[cell_counter] = sym.timestamp


def resolve_rval_symbols(node: Union[str, ast.AST], should_update_usage_info: bool = True) -> Set[DataSymbol]:
    if isinstance(node, str):
        node = ast.parse(node).body[0]
    rval_symbols = ResolveRvalSymbols()(node)
    if should_update_usage_info:
        update_usage_info(rval_symbols)
    return rval_symbols
