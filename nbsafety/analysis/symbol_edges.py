# -*- coding: future_annotations -*-
import ast
from collections import defaultdict
import logging
from typing import Any, List, Sequence, TYPE_CHECKING

from nbsafety.analysis.mixins import SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.singletons import tracer

if TYPE_CHECKING:
    from typing import Dict, Optional, Set, Tuple, Union


logger = logging.getLogger(__name__)


class TiedTuple(tuple):
    """Just a marker class indicating that we should not unpack contents of this tuple"""
    pass


_MULTIPLE_SYMBOL_TYPES = (tuple, TiedTuple)


def _flatten(vals):
    for v in vals:
        if isinstance(v, tuple):
            yield from _flatten(v)
        else:
            yield v


class ResolveDependencies(SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self):
        self.symbols: List[Optional[DataSymbol]] = []

    def __call__(self, node: ast.AST):
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
            # only descend if tracer failed to create literal symbol
            self.generic_visit(node.keys)
            self.generic_visit(node.values)
        else:
            self.symbols.append(resolved)

    def visit_List_or_Tuple(self, node: Union[ast.List, ast.Tuple]):
        resolved = tracer().resolve_loaded_symbol(node)
        if resolved is None:
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


class GetSymbolEdges(SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self):
        # TODO: figure out how to give these type annotations
        self.lval_symbols: List[Any] = []
        self.rval_symbols: List[Any] = []
        self.simple_edges: List[Any] = []
        self.gather_rvals = True

    def __call__(self, node: ast.AST):
        self.visit(node)
        self._collect_simple_edges()
        self.lval_symbols = []
        self.rval_symbols = []
        yield from self.simple_edges

    def get_rval_symbols(self, node: ast.AST):
        self.visit(node)
        return {sym for sym in _flatten(self.rval_symbols) if sym is not None}

    def _collect_simple_edges(self):
        if len(self.lval_symbols) == 0:
            self.lval_symbols.append(None)
        if len(self.rval_symbols) == 0:
            self.rval_symbols.append(None)
        for lval in set(_flatten(self.lval_symbols)):
            for rval in set(_flatten(self.rval_symbols)):
                if lval is None and rval is None:
                    continue
                self.simple_edges.append((lval, rval))

    def gather_lvals_context(self):
        return self.push_attributes(gather_rvals=False)

    def gather_rvals_context(self):
        return self.push_attributes(gather_rvals=True)

    @property
    def to_add_set(self):
        if self.gather_rvals:
            return self.rval_symbols
        else:
            return self.lval_symbols

    @to_add_set.setter
    def to_add_set(self, val):
        if self.gather_rvals:
            self.rval_symbols = val
        else:
            self.lval_symbols = val

    def visit_Name(self, node):
        self.to_add_set.append(node.id)

    def visit_Num(self, node):
        self.visit_Constant(node)

    def visit_Str(self, node):
        self.visit_Constant(node)

    def visit_Constant(self, node):
        self.to_add_set.append(None)

    def visit_NameConstant(self, node):
        self.to_add_set.append(None)

    def visit_Tuple(self, node):
        self.visit_List_or_Tuple(node)

    def visit_List(self, node):
        self.visit_List_or_Tuple(node)

    def visit_Dict(self, node):
        temp = self.to_add_set
        self.to_add_set = []
        self.visit(node.keys)
        self.visit(node.values)
        self.to_add_set, temp = temp, self.to_add_set
        self.to_add_set.append(tuple(temp))

    def visit_List_or_Tuple(self, node):
        temp = self.to_add_set
        self.to_add_set = []
        self.visit(node.elts)
        self.to_add_set, temp = temp, self.to_add_set
        self.to_add_set.append(tuple(temp))

    def visit_expr(self, node):
        if isinstance(node, getattr(ast, 'NamedExpr', None)):
            self.visit_NamedExpr(node)
            return
        assert self.gather_rvals
        temp = self.to_add_set
        self.to_add_set = []
        super().generic_visit(node)
        self.to_add_set, temp = temp, self.to_add_set
        self.to_add_set.append(tuple(temp))

    def visit_NamedExpr(self, node):
        with self.push_attributes(lval_symbols=[], rval_symbols=[]):
            with self.gather_lvals_context():
                self.visit(node.target)
            with self.gather_rvals_context():
                self.visit(node.value)
            rvals_to_extend = self.lval_symbols + self.rval_symbols
            self._collect_simple_edges()
        self.rval_symbols.extend(rvals_to_extend)

    def generic_visit(self, node: Union[ast.AST, Sequence[ast.AST]]):
        # The purpose of this is to make sure we call our visit_expr method if we see an expr
        if node is None:
            return
        elif isinstance(node, ast.expr):
            self.visit_expr(node)
        else:
            super().generic_visit(node)

    def visit_AugAssign_or_AnnAssign(self, node):
        with self.push_attributes(lval_symbols=[], rval_symbols=[]):
            with self.gather_lvals_context():
                self.visit(node.target)
            with self.gather_rvals_context():
                self.visit(node.value)
            self._collect_simple_edges()

    def visit_AnnAssign(self, node):
        self.visit_AugAssign_or_AnnAssign(node)

    def visit_AugAssign(self, node):
        self.visit_AugAssign_or_AnnAssign(node)

    def visit_Call(self, node):
        extra_to_add = []
        if isinstance(node.func, (ast.Attribute, ast.Subscript, ast.Call)):
            # TODO: descend further down
            extra_to_add.append(id(node))
        else:
            assert isinstance(node.func, ast.Name)
            extra_to_add.append(node.func.id)
        temp = self.to_add_set
        self.to_add_set = []
        self.generic_visit([node.args, node.keywords])
        self.to_add_set, temp = temp, self.to_add_set
        temp = TiedTuple(set(_flatten(temp)) | set(extra_to_add))
        self.to_add_set.append(temp)

    def visit_Attribute_or_Subscript(self, node):
        # TODO: we'll ignore args inside of inner calls, e.g. f.g(x, y).h; need to descend further down
        self.to_add_set.append(id(node))

    def visit_Attribute(self, node):
        self.visit_Attribute_or_Subscript(node)

    def visit_Subscript(self, node):
        if self.gather_rvals:
            temp = self.to_add_set
            self.to_add_set = []
            self.visit_Attribute_or_Subscript(node)
            # add slice to RHS to avoid propagating to it
            self.visit(node.slice)
            self.to_add_set, temp = temp, self.to_add_set
            self.to_add_set.append(tuple(temp))
        else:
            self.visit_Attribute_or_Subscript(node)

    # def visit_Subscript(self, node):
    #     self.visit_Attribute_or_Subscript(node)
    #     # TODO: the reason we wanted this before is to avoid propagating to the slice
    #     #  add something back in to avoid propagating to everything on RHS
    #     # if self.gather_rvals:
    #     #     self.visit(node.slice)

    def visit_Keyword(self, node):
        self.visit(node.value)

    def visit_Starred(self, node):
        self.visit(node.value)

    def visit_Lambda(self, node):
        assert self.gather_rvals
        # remove node.arguments
        self.visit(node.body)
        self.visit(node.args)
        with self.push_attributes(rval_symbols=[]):
            self.visit(node.args.args)
            self.visit(node.args.vararg)
            self.visit(node.args.kwonlyargs)
            self.visit(node.args.kwarg)
            discard_set = set(self.rval_symbols)
        # throw away anything appearing in lambda body that isn't bound
        self.rval_symbols = list(set(self.rval_symbols) - discard_set)

    def visit_GeneratorExp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_DictComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_ListComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_SetComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(self, node):
        assert self.gather_rvals
        to_append = set()
        for gen in node.generators:
            if isinstance(gen, ast.comprehension):
                with self.push_attributes(rval_symbols=[]):
                    self.visit(gen.iter)
                    self.visit(gen.ifs)
                    to_append |= set(_flatten(self.rval_symbols))
                with self.push_attributes(rval_symbols=[]):
                    self.visit(gen.target)
                    discard_set = set(self.rval_symbols)
            else:
                with self.push_attributes(rval_symbols=[]):
                    self.visit(gen)
                    discard_set = set(self.rval_symbols)
            to_append -= discard_set
        self.rval_symbols.append(TiedTuple(to_append))

    def visit_arg(self, node):
        self.to_add_set.append(node.arg)

    def visit_For(self, node):
        # skip body -- will have dummy since this visitor works line-by-line
        with self.push_attributes(lval_symbols=[], rval_symbols=[]):
            with self.gather_lvals_context():
                self.visit(node.target)
            with self.gather_rvals_context():
                self.visit(node.iter)
            self._collect_simple_edges()

    def visit_If(self, node):
        # skip body here too
        self.visit(node.test)

    def visit_FunctionDef_or_AsyncFunctionDef(self, node):
        with self.push_attributes(lval_symbols=[], rval_symbols=[]):
            self.lval_symbols.append(node.name)
            with self.gather_rvals_context():
                self.visit(node.args)
                self.visit(node.decorator_list)
            self._collect_simple_edges()

    def visit_FunctionDef(self, node):
        self.visit_FunctionDef_or_AsyncFunctionDef(node)

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef_or_AsyncFunctionDef(node)

    def visit_ClassDef(self, node):
        with self.push_attributes(lval_symbols=[], rval_symbols=[]):
            self.lval_symbols.append(node.name)
            with self.gather_rvals_context():
                self.visit(node.bases)
                self.visit(node.decorator_list)
            self._collect_simple_edges()

    def visit_With(self, node):
        # skip body
        self.visit(node.items)

    def visit_withitem(self, node):
        with self.gather_lvals_context():
            self.visit(node.optional_vars)
        with self.gather_rvals_context():
            self.visit(node.context_expr)

    def visit_Import(self, node: ast.Import):
        self.visit_Import_or_ImportFrom(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        self.visit_Import_or_ImportFrom(node)

    def visit_Import_or_ImportFrom(self, node: Union[ast.Import, ast.ImportFrom]):
        with self.push_attributes(lval_symbols=[], rval_symbols=[]):
            for name in node.names:
                if name.asname is None:
                    if name.name != '*' and '.' not in name.name:
                        self.lval_symbols.append(name.name)
                else:
                    self.lval_symbols.append(name.asname)
            self._collect_simple_edges()


def get_assignment_lval_and_rval_symbol_refs(node: Union[str, ast.AST]):
    if isinstance(node, str):
        node = ast.parse(node).body[0]
    yield from GetSymbolEdges()(node)


# TODO: refine type sig
def get_symbol_edges(node: Union[str, ast.AST]) -> Any:
    if isinstance(node, str):
        node = ast.parse(node).body[0]
    visitor = GetSymbolEdges()
    edges: Dict[Optional[Union[str, int]], Set[Optional[Union[str, int]]]] = defaultdict(set)
    for edge in visitor(node):
        left, right = edge
        edges[left].add(right)
    return edges


def get_symbol_rvals(node: Union[str, ast.AST]) -> Set[Union[int, str, DataSymbol]]:
    if isinstance(node, str):
        node = ast.parse(node).body[0]
    return GetSymbolEdges().get_rval_symbols(node)
