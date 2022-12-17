# -*- coding: utf-8 -*-
import ast
import logging
from contextlib import contextmanager
from typing import Iterable, List, Optional, Set, Union

from ipyflow.analysis.live_refs import static_resolve_rvals
from ipyflow.analysis.mixins import (
    SaveOffAttributesMixin,
    SkipUnboundArgsMixin,
    VisitListsMixin,
)
from ipyflow.analysis.symbol_ref import resolve_slice_to_constant
from ipyflow.data_model.code_cell import cells
from ipyflow.data_model.data_symbol import DataSymbol
from ipyflow.data_model.namespace import Namespace
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.singletons import flow, tracer

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class ResolveRvalSymbols(
    SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor
):
    def __init__(self, update_usage_info: bool) -> None:
        self.symbols: List[Optional[DataSymbol]] = []
        self.update_usage_info = update_usage_info
        self.dead: Set[DataSymbol] = set()
        self._in_kill_context = False
        self.exclude_ns = False

    def __call__(self, node: ast.AST) -> Set[DataSymbol]:
        self.visit(node)
        return {sym for sym in self.symbols if sym is not None}

    def _resolve_symbols_without_side_effects(self, node: ast.AST) -> List[DataSymbol]:
        with self.push_attributes(
            symbols=[], dead=set(), _in_kill_context=False, update_usage_info=False
        ):
            self.visit(node)
            return [sym for sym in self.symbols if sym is not None]

    @contextmanager
    def killed_context(self, node):
        dead = self.dead
        with self.push_attributes(_in_kill_context=True, dead=set()):
            self.visit(node)
            new_dead = self.dead
            not_present_before = {ref for ref in new_dead if ref not in dead}
        self.dead |= new_dead
        try:
            yield
        finally:
            self.dead -= not_present_before

    def _update_usage_info(
        self,
        symbols: Iterable[DataSymbol],
        used_node: ast.AST,
        should_extend: bool = True,
    ) -> None:
        symbols = set(symbols)
        if self._in_kill_context:
            self.dead |= set(symbols)
        elif self.update_usage_info:
            symbols = [symbol for symbol in symbols if symbol not in self.dead]
            Timestamp.update_usage_info(
                symbols, used_node=used_node, exclude_ns=self.exclude_ns
            )
            if should_extend:
                self.symbols.extend(symbols)

    def visit_Name(self, node: ast.Name):
        resolved = tracer().resolve_loaded_symbols(node)
        self._update_usage_info(resolved, node)

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
            self._update_usage_info(resolved, node)

    def visit_List_or_Tuple(self, node: Union[ast.List, ast.Tuple]):
        resolved = tracer().resolve_loaded_symbols(node)
        if not resolved:
            # if id(node) not in tracer().node_id_to_loaded_literal_scope:
            # only descend if tracer failed to create literal symbol
            self.generic_visit(node.elts)
        else:
            self._update_usage_info(resolved, node)

    def visit_AugAssign_or_AnnAssign(self, node):
        self.visit(node.value)

    def visit_AnnAssign(self, node):
        self.visit_AugAssign_or_AnnAssign(node)

    def visit_AugAssign(self, node):
        self.visit_AugAssign_or_AnnAssign(node)

    def visit_Call(self, node):
        if isinstance(node.func, (ast.Attribute, ast.Subscript)):
            self.visit(node.func)
        resolved = tracer().resolve_loaded_symbols(node.func)
        self._update_usage_info(resolved, node.func)
        resolved = tracer().resolve_loaded_symbols(node)
        self._update_usage_info(resolved, node)
        self.generic_visit([node.args, node.keywords])

    def _get_attr_or_subscript_namespace(
        self, node: Union[ast.Attribute, ast.Subscript]
    ) -> Optional[Namespace]:
        symbols = self._resolve_symbols_without_side_effects(node.value)
        if len(symbols) != 1 or symbols[0] is None:
            return None
        return flow().namespaces.get(symbols[0].obj_id, None)

    def visit_Attribute(self, node: ast.Attribute):
        if isinstance(node.value, ast.Call):
            self.visit(node.value)
        symbols = tracer().resolve_loaded_symbols(node)
        if len(symbols) > 0:
            self._update_usage_info(symbols, node)
            return
        # TODO: this path lacks coverage
        try:
            ns = self._get_attr_or_subscript_namespace(node)
            if ns is None:
                return
            dsym = ns.lookup_data_symbol_by_name_this_indentation(
                node.attr, is_subscript=False
            )
            if dsym is not None:
                self._update_usage_info([dsym], node)
        except Exception:
            logger.exception(
                "Exception occurred while resolving node %s", ast.dump(node)
            )

    def visit_Subscript(self, node: ast.Subscript):
        if isinstance(node.value, ast.Call):
            self.visit(node.value)
        symbols = tracer().resolve_loaded_symbols(node)
        self._update_usage_info(symbols, node, should_extend=False)
        # add slice to RHS to avoid propagating to it
        symbols.extend(self._resolve_symbols_without_side_effects(node.slice))
        if len(symbols) > 0:
            self.symbols.extend(symbols)
            return
        # TODO: this path lacks coverage
        try:
            slice = resolve_slice_to_constant(node)
            if slice is None or isinstance(slice, ast.Name):
                return
            symbols = self._resolve_symbols_without_side_effects(node.value)
            if len(symbols) != 1 or symbols[0] is None:
                return
            ns = self._get_attr_or_subscript_namespace(node)
            if ns is None:
                return
            dsym = ns.lookup_data_symbol_by_name_this_indentation(
                slice, is_subscript=True
            )
            if dsym is None and isinstance(slice, int) and slice < 0:
                try:
                    dsym = ns.lookup_data_symbol_by_name_this_indentation(
                        len(ns) + slice, is_subscript=True
                    )
                except TypeError:
                    dsym = None
            if dsym is not None:
                self._update_usage_info([dsym], node)
        except Exception:
            logger.exception(
                "Exception occurred while resolving node %s", ast.dump(node)
            )

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
        with self.killed_context([gen.target for gen in node.generators]):
            if isinstance(node, ast.DictComp):
                self.visit(node.key)
                self.visit(node.value)
            else:
                self.visit(node.elt)
            for gen in node.generators:
                self.visit(gen.iter)
                self.visit(gen.ifs)

    def visit_arg(self, node: ast.arg):
        resolved = tracer().resolve_loaded_symbols(node.arg)
        self._update_usage_info(resolved, node)

    def visit_For(self, node: ast.For):
        # skip body -- will have dummy since this visitor works line-by-line
        self.visit(node.iter)

    def visit_If(self, node: ast.If):
        # skip body here too
        self.visit(node.test)

    def visit_FunctionDef_or_AsyncFunctionDef(
        self, node: Union[ast.AsyncFunctionDef, ast.FunctionDef]
    ):
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
            resolved = tracer().resolve_loaded_symbols(node.type)
            self._update_usage_info(resolved, node.type)

    def visit_Import(self, node: ast.Import):
        pass

    def visit_ImportFrom(self, node: ast.ImportFrom):
        pass


def resolve_rval_symbols(
    node: Union[str, ast.AST], should_update_usage_info: bool = True
) -> Set[DataSymbol]:
    if isinstance(node, str):
        node = ast.parse(node).body[0]
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        node = node.value
    rval_symbols = ResolveRvalSymbols(should_update_usage_info)(node)
    if len(rval_symbols) == 0:
        prev_cell = cells().current_cell().prev_cell
        static_rval_symbols = static_resolve_rvals(
            node, cell_ctr=-1 if prev_cell is None else prev_cell.cell_ctr
        )
        if should_update_usage_info:
            Timestamp.update_usage_info(static_rval_symbols, used_node=node)
        rval_symbols = {sym.dsym for sym in static_rval_symbols}
    return rval_symbols
