# -*- coding: future_annotations -*-
import ast
import logging
from typing import List, TYPE_CHECKING

from nbsafety.analysis.mixins import SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.singletons import tracer

if TYPE_CHECKING:
    from typing import Dict, Optional, Set, Tuple, Union


logger = logging.getLogger(__name__)


class ResolveRvalSymbols(SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self):
        self.symbols: List[Optional[DataSymbol]] = []

    def __call__(self, node: ast.AST) -> Set[DataSymbol]:
        self.visit(node)
        return {sym for sym in self.symbols if sym is not None}

    def _push_symbols(self):
        return self.push_attributes(symbols=[])

    def visit_Name(self, node: ast.Name):
        self.symbols.append(tracer().resolve_loaded_symbol(node))

    def visit_Tuple(self, node: ast.Tuple):
        self.visit_List_or_Tuple(node)

    def visit_List(self, node: ast.List):
        self.visit_List_or_Tuple(node)

    def visit_Dict(self, node: ast.Dict):
        resolved = tracer().resolve_loaded_symbol(node)
        if resolved is None:
            # if id(node) not in tracer().node_id_to_loaded_literal_scope:
            # only descend if tracer failed to create literal symbol
            self.generic_visit(node.keys)
            self.generic_visit(node.values)
        else:
            self.symbols.append(resolved)

    def visit_List_or_Tuple(self, node: Union[ast.List, ast.Tuple]):
        resolved = tracer().resolve_loaded_symbol(node)
        if resolved is None:
            # if id(node) not in tracer().node_id_to_loaded_literal_scope:
            # only descend if tracer failed to create literal symbol
            self.generic_visit(node.elts)
        else:
            self.symbols.append(resolved)

    def visit_AugAssign_or_AnnAssign(self, node):
        self.visit(node.value)

    def visit_AnnAssign(self, node):
        self.visit_AugAssign_or_AnnAssign(node)

    def visit_AugAssign(self, node):
        self.visit_AugAssign_or_AnnAssign(node)

    def visit_Call(self, node):
        # TODO: descend further down
        self.symbols.append(tracer().resolve_loaded_symbol(node.func))
        self.symbols.append(tracer().resolve_loaded_symbol(node))
        self.generic_visit([node.args, node.keywords])

    def visit_Attribute(self, node: ast.Attribute):
        # TODO: we'll ignore args inside of inner calls, e.g. f.g(x, y).h; need to descend further down
        self.symbols.append(tracer().resolve_loaded_symbol(node))

    def visit_Subscript(self, node: ast.Subscript):
        # TODO: we'll ignore args inside of inner calls, e.g. f.g(x, y).h; need to descend further down
        self.symbols.append(tracer().resolve_loaded_symbol(node))
        # add slice to RHS to avoid propagating to it
        self.visit(node.slice)

    def visit_keyword(self, node: ast.keyword):
        self.visit(node.value)

    def visit_Starred(self, node: ast.Starred):
        self.symbols.append(tracer().resolve_loaded_symbol(node))

    def visit_Lambda(self, node):
        with self._push_symbols():
            self.visit(node.body)
            self.visit(node.args)
            to_add = set(self.symbols)
        # remove node.arguments
        with self._push_symbols():
            self.visit(node.args.args)
            self.visit(node.args.vararg)
            self.visit(node.args.kwonlyargs)
            self.visit(node.args.kwarg)
            discard_set = set(self.symbols)
        # throw away anything appearing in lambda body that isn't bound
        self.symbols.extend(to_add - discard_set)

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
        self.symbols.append(tracer().resolve_loaded_symbol(node.arg))

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

    def visit_Import(self, node: ast.Import):
        pass

    def visit_ImportFrom(self, node: ast.ImportFrom):
        pass


def resolve_rval_symbols(node: Union[str, ast.AST]) -> Set[DataSymbol]:
    if isinstance(node, str):
        node = ast.parse(node).body[0]
    return ResolveRvalSymbols()(node)
