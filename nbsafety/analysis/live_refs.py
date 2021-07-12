# -*- coding: future_annotations -*-
import ast
import itertools
import logging
from typing import cast, TYPE_CHECKING

from nbsafety.analysis.attr_symbols import get_attrsub_symbol_chain, AttrSubSymbolChain, CallPoint
from nbsafety.analysis.mixins import SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin
from nbsafety.data_model.data_symbol import DataSymbol
from nbsafety.data_model.scope import Scope
from nbsafety.data_model.timestamp import Timestamp

if TYPE_CHECKING:
    from typing import Generator, Iterable, List, Optional, Set, Tuple, Union
    from nbsafety.types import SymbolRef

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


# TODO: have the logger warnings additionally raise exceptions for tests
class ComputeLiveSymbolRefs(SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor):
    def __init__(self, scope: Optional[Scope] = None, init_killed: Optional[Set[str]] = None):
        self._scope = scope
        self._module_stmt_counter = 0
        # live symbols also include the stmt counter of when they were live, for slicing purposes later
        self.live: Set[Tuple[SymbolRef, int]] = set()
        if init_killed is None:
            self.dead: Set[SymbolRef] = set()
        else:
            self.dead = cast('Set[SymbolRef]', init_killed)
        # TODO: use the ast context instead of hacking our own (e.g. ast.Load(), ast.Store(), etc.)
        self._in_kill_context = False
        self._inside_attrsub = False
        self._skip_simple_names = False

    def __call__(self, node: ast.AST):
        """
        This function should be called when we want to do a liveness check on a
        cell's corresponding ast.Module.
        """
        # TODO: this will break if we ref a variable in a loop before killing it in the
        #   same loop, since we will add everything on the LHS of an assignment to the killed
        #   set before checking the loop body for live variables
        self.visit(node)
        return self.live, self.dead

    def kill_context(self):
        return self.push_attributes(_in_kill_context=True)

    def live_context(self):
        return self.push_attributes(_in_kill_context=False)

    def attrsub_context(self, inside=True):
        return self.push_attributes(_inside_attrsub=inside, _skip_simple_names=inside)

    def args_context(self):
        return self.push_attributes(_skip_simple_names=False)

    def _add_attrsub_to_live_if_eligible(self, ref: AttrSubSymbolChain):
        if ref in self.dead:
            return
        if len(ref.symbols) == 0:
            # can happen if user made syntax error like [1, 2, 3][4, 5, 6] (e.g. forgot comma)
            return
        leading_symbol = ref.symbols[0]
        if isinstance(leading_symbol, str) and leading_symbol in self.dead:
            return
        if isinstance(leading_symbol, CallPoint) and leading_symbol.symbol in self.dead:
            return
        self.live.add((ref, self._module_stmt_counter))

    # the idea behind this one is that we don't treat a symbol as dead
    # if it is used on the RHS of an assignment
    def visit_Assign_impl(self, targets, value, aug_assign_target=None):
        this_assign_live = set()
        # we won't mutate overall dead for visiting simple targets, and we need it to avoid adding false positive lives
        with self.push_attributes(live=this_assign_live):
            self.visit(value)
            if aug_assign_target is not None:
                self.visit(aug_assign_target)
        # make a copy, then track the new dead
        this_assign_dead = set(self.dead)
        with self.push_attributes(dead=this_assign_dead):
            with self.kill_context():
                for target in targets:
                    self.visit_Assign_target(target)
        this_assign_dead -= self.dead
        # TODO: ideally under the current abstraction we should
        #  not be resolving static references to symbols here
        if (
            self._scope is not None
            and len(this_assign_live) == 1
            and len(this_assign_dead) == 1
            and not (this_assign_dead <= self.dead)
            and aug_assign_target is None
            and isinstance(value, (ast.Attribute, ast.Subscript, ast.Name))
        ):
            lhs, rhs = [
                get_symbols_for_references(x, self._scope, only_yield_successful_resolutions=True)[0]
                for x in (this_assign_dead, (live[0] for live in this_assign_live))
            ]
            if len(lhs) == 1 and len(rhs) == 1:
                lhs, rhs = [next(iter(x)) for x in (lhs, rhs)]
                # hack to avoid marking `b` as live when objects are same,
                # or when it was detected that rhs symbol wasn't actually updated
                if lhs.obj is rhs.obj:
                    # it's a no-op, so treat it as such
                    this_assign_live.clear()
                    this_assign_dead.clear()
        this_assign_dead -= {live[0] for live in this_assign_live}
        # for ref in this_assign_dead:
        #     if isinstance(ref, AttrSubSymbolChain) and len(ref.symbols) > 1:
        #         this_assign_live.add(
        #             (AttrSubSymbolChain(list(ref.symbols[:-1]) + [
        #                 # FIXME: hack to ensure it can't be resolved all the way, so that we use
        #                 #  timestamp_excluding_ns_children instead of timestamp
        #                 CallPoint('<dummy>')
        #             ]), self._module_stmt_counter)
        #         )
        self.live |= this_assign_live
        self.dead |= this_assign_dead

    def visit_NamedExpr(self, node):
        self.visit_Assign_impl([node.target], node.value)

    def visit_Assign(self, node: ast.Assign):
        self.visit_Assign_impl(node.targets, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        self.visit_Assign_impl([node.target], node.value)

    def visit_AugAssign(self, node: ast.AugAssign):
        self.visit_Assign_impl([], node.value, aug_assign_target=node.target)

    def visit_Assign_target(
        self, target_node: Union[ast.Attribute, ast.Name, ast.Subscript, ast.Tuple, ast.List, ast.expr]
    ):
        if isinstance(target_node, ast.Name):
            self.dead.add(target_node.id)
        elif isinstance(target_node, (ast.Attribute, ast.Subscript)):
            self.dead.add(get_attrsub_symbol_chain(target_node))
            if isinstance(target_node, ast.Subscript):
                with self.live_context():
                    self.visit(target_node.slice)
        elif isinstance(target_node, (ast.Tuple, ast.List)):
            for elt in target_node.elts:
                self.visit_Assign_target(elt)
        elif isinstance(target_node, ast.Starred):
            self.visit_Assign_target(target_node.value)
        else:
            logger.warning('unsupported type for node %s' % target_node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.generic_visit(node.args.defaults)
        self.generic_visit(node.decorator_list)
        self.dead.add(node.name)

    def visit_Name(self, node):
        if self._in_kill_context:
            self.dead.add(node.id)
        elif not self._skip_simple_names and node.id not in self.dead:
            self.live.add((node.id, self._module_stmt_counter))

    def visit_Tuple_or_List(self, node):
        for elt in node.elts:
            self.visit(elt)

    def visit_List(self, node):
        self.visit_Tuple_or_List(node)

    def visit_Tuple(self, node):
        self.visit_Tuple_or_List(node)

    def visit_For(self, node: ast.For):
        # Case "for a,b in something: "
        self.visit(node.iter)
        with self.kill_context():
            self.visit(node.target)
        for line in node.body:
            self.visit(line)

    def visit_ClassDef(self, node: ast.ClassDef):
        self.generic_visit(node.bases)
        self.generic_visit(node.decorator_list)
        self.dead.add(node.name)

    def visit_Call(self, node: ast.Call):
        with self.args_context():
            self.generic_visit(node.args)
            for kwarg in node.keywords:
                self.visit(kwarg.value)
        self._add_attrsub_to_live_if_eligible(get_attrsub_symbol_chain(node))
        if isinstance(node.func, (ast.Attribute, ast.Subscript)):
            with self.attrsub_context():
                self.visit(node.func)
        else:
            self.visit(node.func)

    def visit_Attribute(self, node: ast.Attribute):
        if not self._inside_attrsub:
            self._add_attrsub_to_live_if_eligible(get_attrsub_symbol_chain(node))
        with self.attrsub_context():
            self.visit(node.value)

    def visit_Subscript(self, node: ast.Subscript):
        if not self._inside_attrsub:
            self._add_attrsub_to_live_if_eligible(get_attrsub_symbol_chain(node))
        with self.attrsub_context():
            self.visit(node.value)
        with self.attrsub_context(inside=False):
            self.visit(node.slice)

    def visit_GeneratorExp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_DictComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_ListComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_SetComp(self, node):
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(self, node):
        # TODO: as w/ for loop, this will have false positives on later live references
        for gen in node.generators:
            self.visit(gen.iter)
            with self.kill_context():
                self.visit(gen.target)
        # visit the elt at the end to ensure we don't add it to live vars if it was one of the generator targets
        self.visit(node.elt)

    def visit_Lambda(self, node):
        with self.kill_context():
            self.visit(node.args)

    def visit_arg(self, node):
        if self._in_kill_context:
            self.dead.add(node.arg)
        elif not self._skip_simple_names and node.arg not in self.dead:
            self.live.add((node.arg, self._module_stmt_counter))

    def visit_Module(self, node: ast.Module):
        for child in node.body:
            assert isinstance(child, ast.stmt)
            self.visit(child)
            self._module_stmt_counter += 1


def gen_symbols_for_references(
    symbol_refs: Iterable[SymbolRef],
    scope: Scope,
    only_yield_successful_resolutions: bool,
    stmt_counters: Optional[Iterable[int]] = None,
    update_liveness_time_versions: bool = False,
) -> Generator[Tuple[DataSymbol, bool, bool, Optional[int]], None, None]:
    if stmt_counters is None:
        stmt_counters = (None for _ in itertools.count())
    for symbol_ref, stmt_counter in zip(symbol_refs, stmt_counters):
        if isinstance(symbol_ref, str):
            dsym = scope.lookup_data_symbol_by_name(symbol_ref)
            if dsym is not None:
                yield dsym, False, True, stmt_counter
        elif isinstance(symbol_ref, AttrSubSymbolChain):
            if update_liveness_time_versions:
                # TODO: only use this branch one staleness checker can be smarter about liveness timestamps.
                #  Right now, yielding the intermediate elts of the chain will yield false positives in the
                #  event of namespace stale children.
                for dsym, is_called, success in scope.gen_data_symbols_for_attrsub_chain(symbol_ref):
                    if only_yield_successful_resolutions and not success:
                        continue
                    yield dsym, is_called, success, stmt_counter
            else:
                dsym, is_called, success = scope.get_most_specific_data_symbol_for_attrsub_chain(symbol_ref)
                if dsym is not None:
                    if success or not only_yield_successful_resolutions:
                        yield dsym, is_called, success, stmt_counter
        else:
            logger.warning('invalid type for ref %s', symbol_ref)
            continue


def get_symbols_for_references(
    symbol_refs: Iterable[SymbolRef],
    scope: Scope,
    only_yield_successful_resolutions: bool = False,
) -> Tuple[Set[DataSymbol], Set[DataSymbol]]:
    dsyms: Set[DataSymbol] = set()
    called_dsyms: Set[DataSymbol] = set()
    for dsym, is_called, *_ in gen_symbols_for_references(
        symbol_refs, scope, only_yield_successful_resolutions=only_yield_successful_resolutions
    ):
        if is_called:
            called_dsyms.add(dsym)
        else:
            dsyms.add(dsym)
    return dsyms, called_dsyms


def get_live_symbols_and_cells_for_references(
    symbol_refs: Set[Tuple[SymbolRef, int]],
    scope: Scope,
    cell_ctr: int,
    update_liveness_time_versions: bool = False,
) -> Tuple[Set[DataSymbol], Set[int]]:
    dsyms: Set[DataSymbol] = set()
    called_dsyms: Set[Tuple[DataSymbol, int]] = set()
    only_symbol_refs = (ref[0] for ref in symbol_refs)
    only_stmt_counters = (ref[1] for ref in symbol_refs)
    for dsym, is_called, success, stmt_ctr in gen_symbols_for_references(
        only_symbol_refs,
        scope,
        only_yield_successful_resolutions=False,
        stmt_counters=only_stmt_counters,
        update_liveness_time_versions=update_liveness_time_versions,
    ):
        if update_liveness_time_versions:
            ts_to_use = dsym.timestamp if success else dsym.timestamp_excluding_ns_descendents
            dsym.timestamp_by_liveness_time_by_cell_counter[cell_ctr][Timestamp(cell_ctr, stmt_ctr)] = ts_to_use
        if is_called:
            called_dsyms.add((dsym, stmt_ctr))
        else:
            dsyms.add(dsym)
    live_from_calls, live_cells = _compute_call_chain_live_symbols_and_cells(
        called_dsyms, cell_ctr, update_liveness_time_versions
    )
    dsyms |= live_from_calls
    return dsyms, live_cells


def _compute_call_chain_live_symbols_and_cells(
    live_with_stmt_ctr: Set[Tuple[DataSymbol, int]], cell_ctr: int, update_liveness_time_versions: bool
) -> Tuple[Set[DataSymbol], Set[int]]:
    seen = set()
    worklist = list(live_with_stmt_ctr)
    live = {dsym_stmt[0] for dsym_stmt in live_with_stmt_ctr}
    while len(worklist) > 0:
        workitem = worklist.pop()
        if workitem in seen:
            continue
        called_dsym, stmt_ctr = workitem
        # TODO: handle callable classes
        if not called_dsym.is_function:
            continue
        seen.add(workitem)
        live_refs, _ = compute_live_dead_symbol_refs(
            cast(ast.FunctionDef, called_dsym.stmt_node).body, init_killed=set(called_dsym.get_definition_args())
        )
        used_time = Timestamp(cell_ctr, stmt_ctr)
        for dsym, is_called, success, *_ in gen_symbols_for_references(
            (ref[0] for ref in live_refs), called_dsym.call_scope, only_yield_successful_resolutions=False
        ):
            if is_called:
                worklist.append((dsym, stmt_ctr))
            if dsym.is_globally_accessible:
                live.add(dsym)
                if update_liveness_time_versions:
                    ts_to_use = dsym.timestamp if success else dsym.timestamp_excluding_ns_descendents
                    dsym.timestamp_by_liveness_time_by_cell_counter[cell_ctr][used_time] = ts_to_use
    return live, {called_dsym.timestamp.cell_num for called_dsym, _ in seen}


def compute_live_dead_symbol_refs(
    code: Union[ast.AST, List[ast.stmt], str],
    scope: Scope = None,
    init_killed: Optional[Set[str]] = None,
) -> Tuple[Set[Tuple[SymbolRef, int]], Set[SymbolRef]]:
    if init_killed is None:
        init_killed = set()
    if isinstance(code, str):
        code = ast.parse(code)
    elif isinstance(code, list):
        code = ast.Module(code)
    return ComputeLiveSymbolRefs(scope=scope, init_killed=init_killed)(code)
