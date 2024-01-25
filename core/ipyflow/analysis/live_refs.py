# -*- coding: utf-8 -*-
import ast
import builtins
import logging
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterable, List, Optional, Set, Tuple, Union, cast

from ipyflow.analysis.mixins import (
    SaveOffAttributesMixin,
    SkipUnboundArgsMixin,
    VisitListsMixin,
)
from ipyflow.analysis.resolved_symbols import ResolvedSymbol
from ipyflow.analysis.symbol_ref import Atom, LiveSymbolRef, SymbolRef
from ipyflow.config import FlowDirection
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.singletons import flow, tracer

if TYPE_CHECKING:
    from ipyflow.data_model.scope import Scope
    from ipyflow.data_model.symbol import Symbol

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


_RESOLVER_EXCEPTIONS = ("get_ipython", "run_line_magic", "run_cell_magic")


# TODO: have the logger warnings additionally raise exceptions for tests
class ComputeLiveSymbolRefs(
    SaveOffAttributesMixin, SkipUnboundArgsMixin, VisitListsMixin, ast.NodeVisitor
):
    def __init__(
        self,
        scope: Optional["Scope"] = None,
        init_killed: Optional[Set[str]] = None,
        include_killed_live: bool = False,
    ) -> None:
        self._scope = scope
        self._module_stmt_counter = 0
        # live symbols also include the stmt counter of when they were live, for slicing purposes later
        self.live: Set[LiveSymbolRef] = set()
        self.dead = {
            SymbolRef.from_string(killed, scope=scope)
            for killed in init_killed or set()
        }
        self.modified: Set[SymbolRef] = set()
        # TODO: use the ast context instead of hacking our own (e.g. ast.Load(), ast.Store(), etc.)
        self._in_kill_context = False
        self._inside_attrsub = False
        self._skip_simple_names = False
        self._is_lhs = False
        self._include_killed_live = include_killed_live

    def __call__(
        self, node: ast.AST
    ) -> Tuple[Set[LiveSymbolRef], Set[SymbolRef], Set[SymbolRef]]:
        """
        This function should be called when we want to do a liveness check on a
        cell's corresponding ast.Module.
        """
        # TODO: this will break if we ref a variable in a loop before killing it in the
        #   same loop, since we will add everything on the LHS of an assignment to the killed
        #   set before checking the loop body for live variables
        self.visit(node)
        self.modified |= self.dead
        return self.live, self.dead, self.modified

    def kill_context(self):
        return self.push_attributes(_in_kill_context=True)

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

    def live_context(self):
        return self.push_attributes(_in_kill_context=False)

    def attrsub_context(self, inside=True):
        return self.push_attributes(_inside_attrsub=inside, _skip_simple_names=inside)

    def _add_attrsub_to_live_if_eligible(self, ref: SymbolRef) -> None:
        is_killed = ref.nonreactive() in self.dead
        if is_killed and not self._include_killed_live:
            return
        if len(ref.chain) == 0:
            # can happen if user made syntax error like [1, 2, 3][4, 5, 6] (e.g. forgot comma)
            return
        leading_atom = ref.chain[0]
        if isinstance(leading_atom.value, str):
            is_killed = is_killed or (
                SymbolRef(leading_atom.nonreactive(), scope=self._scope) in self.dead
                or SymbolRef(
                    Atom(leading_atom.value, is_callpoint=False), scope=self._scope
                )
                in self.dead
            )
            if is_killed and not self._include_killed_live:
                return
            self.live.add(
                LiveSymbolRef(
                    ref,
                    self._module_stmt_counter,
                    is_lhs_ref=self._is_lhs,
                    is_killed=is_killed,
                )
            )

    # the idea behind this one is that we don't treat a symbol as dead
    # if it is used on the RHS of an assignment
    def visit_Assign_impl(self, targets, value, aug_assign_target=None) -> None:
        this_assign_live: Set[LiveSymbolRef] = set()
        # we won't mutate overall dead for visiting simple targets, and we need it to avoid adding false positive lives
        with self.push_attributes(live=this_assign_live):
            if aug_assign_target is not None:
                self.visit(aug_assign_target)
                self.modified |= {ref.ref for ref in this_assign_live}
            if value is not None:
                self.visit(value)
            with self.push_attributes(_is_lhs=True):
                for target in targets:
                    if isinstance(target, (ast.Attribute, ast.Subscript)):
                        self.visit(target.value)
        # make a copy, then track the new dead
        this_assign_dead = set(self.dead)
        with self.push_attributes(dead=this_assign_dead):
            # TODO: handle static dead nested symbols more generically
            with self.kill_context():
                for target in targets:
                    self.visit_Assign_target(target)
            if (
                len(targets) == 1
                and isinstance(targets[0], ast.Name)
                and isinstance(value, (ast.List, ast.Tuple))
            ):
                for idx in range(len(value.elts)):
                    this_assign_dead.add(
                        SymbolRef.from_string(
                            f"{targets[0].id}[{idx}]", scope=self._scope
                        )
                    )
        this_assign_dead -= self.dead
        # TODO: ideally under the current abstraction we should
        #  not be resolving static references to symbols here
        if (
            flow().mut_settings.flow_order == FlowDirection.ANY_ORDER
            and self._scope is not None
            and len(this_assign_live) == 1
            and len(this_assign_dead) == 1
            and not (this_assign_dead <= self.dead)
            and aug_assign_target is None
            and value is not None
            and isinstance(value, (ast.Attribute, ast.Subscript, ast.Name))
        ):
            lhs, rhs = [
                get_symbols_for_references(x, self._scope)[0]
                for x in (this_assign_dead, (live.ref for live in this_assign_live))
            ]
            if len(lhs) == 1 and len(rhs) == 1:
                syms: List[Symbol] = [next(iter(x)) for x in (lhs, rhs)]
                lhs_sym, rhs_sym = syms[0], syms[1]
                # hack to avoid marking `b` as live when objects are same,
                # or when it was detected that rhs symbol wasn't actually updated
                if (
                    lhs_sym.obj is rhs_sym.obj
                    or lhs_sym.shallow_timestamp > rhs_sym.timestamp
                ):
                    # either (a) it's a no-op (so treat it as such), or
                    #        (b) lhs is newer and it doesn't make sense to refresh
                    this_assign_live.clear()
        this_assign_dead -= {live.ref for live in this_assign_live}
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

    if sys.version_info >= (3, 8):

        def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
            self.visit_Assign_impl([node.target], node.value)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit_Assign_impl(node.targets, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit_Assign_impl([node.target], node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit_Assign_impl([], node.value, aug_assign_target=node.target)

    def visit_Import(self, node: ast.Import) -> None:
        self.visit_Import_or_ImportFrom(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.visit_Import_or_ImportFrom(node)

    def visit_Import_or_ImportFrom(
        self, node: Union[ast.Import, ast.ImportFrom]
    ) -> None:
        targets = []
        for name in node.names:
            if name.name == "*":
                continue
            targets.append(ast.Name(id=name.asname or name.name, ctx=ast.Store()))
        self.visit_Assign_impl(targets, value=None)

    def visit_Assign_target(
        self,
        target_node: Union[
            ast.Attribute, ast.Name, ast.Subscript, ast.Tuple, ast.List, ast.expr
        ],
    ) -> None:
        if isinstance(target_node, (ast.Name, ast.Attribute, ast.Subscript)):
            self.dead.add(SymbolRef(target_node, scope=self._scope).nonreactive())
            if isinstance(target_node, ast.Subscript):
                with self.live_context():
                    self.visit(target_node.slice)
        elif isinstance(target_node, (ast.Tuple, ast.List)):
            for elt in target_node.elts:
                self.visit_Assign_target(elt)
        elif isinstance(target_node, ast.Starred):
            self.visit_Assign_target(target_node.value)
        else:
            logger.warning("unsupported type for node %s" % target_node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.generic_visit(node.args.defaults)
        self.generic_visit(node.decorator_list)
        self.dead.add(SymbolRef(node).nonreactive())

    def visit_withitem(self, node: ast.withitem):
        self.visit(node.context_expr)
        if node.optional_vars is not None:
            with self.kill_context():
                self.visit(node.optional_vars)

    def visit_Name(self, node: ast.Name) -> None:
        ref = SymbolRef(node, scope=self._scope)
        if self._in_kill_context:
            self.dead.add(ref.nonreactive())
        elif not self._skip_simple_names:
            is_killed = ref in self.dead
            if is_killed and not self._include_killed_live:
                return
            if id(node) in tracer().reactive_node_ids:
                ref.chain[0].is_reactive = True
            self.live.add(
                LiveSymbolRef(
                    ref,
                    self._module_stmt_counter,
                    is_lhs_ref=self._is_lhs,
                    is_killed=is_killed,
                )
            )

    def visit_Tuple_or_List(self, node: Union[ast.List, ast.Tuple]) -> None:
        with self.attrsub_context(False):
            for elt in node.elts:
                self.visit(elt)

    def visit_List(self, node: ast.List) -> None:
        self.visit_Tuple_or_List(node)

    def visit_Tuple(self, node: ast.Tuple) -> None:
        self.visit_Tuple_or_List(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        with self.attrsub_context(False):
            self.generic_visit(node.keys)
            self.generic_visit(node.values)

    def visit_For(self, node: ast.For) -> None:
        # Case "for a,b in something: "
        self.visit(node.iter)
        with self.kill_context():
            self.visit(node.target)
        for line in node.body:
            self.visit(line)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.generic_visit(node.bases)
        self.generic_visit(node.decorator_list)
        self.generic_visit(node.body)
        self.dead.add(SymbolRef(node).nonreactive())

    def visit_Call(self, node: ast.Call) -> None:
        with self.attrsub_context(False):
            self.generic_visit(node.args)
            for kwarg in node.keywords:
                self.visit(kwarg.value)
        if not self._inside_attrsub:
            self._add_attrsub_to_live_if_eligible(SymbolRef(node))
        with self.attrsub_context():
            self.visit(node.func)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if not self._inside_attrsub:
            self._add_attrsub_to_live_if_eligible(SymbolRef(node))
        with self.attrsub_context():
            self.visit(node.value)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if not self._inside_attrsub:
            self._add_attrsub_to_live_if_eligible(SymbolRef(node))
        with self.attrsub_context():
            self.visit(node.value)
        with self.attrsub_context(inside=False):
            self.visit(node.slice)

    def visit_Delete(self, node: ast.Delete) -> None:
        pass

    def visit_GeneratorExp(self, node) -> None:
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_DictComp(self, node) -> None:
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_ListComp(self, node) -> None:
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_SetComp(self, node) -> None:
        self.visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(node)

    def visit_GeneratorExp_or_DictComp_or_ListComp_or_SetComp(self, node) -> None:
        with self.killed_context([gen.target for gen in node.generators]):
            if isinstance(node, ast.DictComp):
                self.visit(node.key)
                self.visit(node.value)
            else:
                self.visit(node.elt)
            for gen in node.generators:
                self.visit(gen.iter)
                self.visit(gen.ifs)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        with self.kill_context():
            self.visit(node.args)

    def visit_arg(self, node) -> None:
        ref = SymbolRef(node.arg, scope=self._scope)
        if self._in_kill_context:
            self.dead.add(ref.nonreactive())
        elif not self._skip_simple_names:
            is_killed = ref in self.dead
            if is_killed and not self._include_killed_live:
                return
            self.live.add(
                LiveSymbolRef(
                    ref,
                    self._module_stmt_counter,
                    is_lhs_ref=self._is_lhs,
                    is_killed=is_killed,
                )
            )

    def visit_Module(self, node: ast.Module) -> None:
        for child in node.body:
            assert isinstance(child, ast.stmt)
            self.visit(child)
            self._module_stmt_counter += 1


def get_symbols_for_references(
    symbol_refs: Iterable[SymbolRef],
    scope: "Scope",
) -> Tuple[Set["Symbol"], Set["Symbol"]]:
    syms: Set[Symbol] = set()
    called_syms: Set["Symbol"] = set()
    for symbol_ref in symbol_refs:
        for resolved in symbol_ref.gen_resolved_symbols(
            scope, only_yield_final_symbol=True
        ):
            if resolved.is_called:
                called_syms.add(resolved.sym)
            else:
                syms.add(resolved.sym)
    return syms, called_syms


def get_live_symbols_and_cells_for_references(
    symbol_refs: Set[LiveSymbolRef],
    scope: "Scope",
    cell_ctr: int,
    update_liveness_time_versions: bool = False,
) -> Tuple[Set[ResolvedSymbol], Set[int], Set[LiveSymbolRef]]:
    live_symbols: Set[ResolvedSymbol] = set()
    unresolved_live_refs: Set[LiveSymbolRef] = set()
    called_syms: Set[Tuple[ResolvedSymbol, int]] = set()
    for live_symbol_ref in symbol_refs:
        chain = live_symbol_ref.ref.chain
        if len(chain) >= 1:
            atom = chain[0].value
            did_resolve = isinstance(atom, str) and (
                hasattr(builtins, atom) or atom in _RESOLVER_EXCEPTIONS
            )
        else:
            did_resolve = False
        resolved = None
        for resolved in live_symbol_ref.gen_resolved_symbols(
            scope,
            only_yield_final_symbol=False,
            yield_all_intermediate_symbols=True,
            cell_ctr=cell_ctr,
        ):
            if (
                live_symbol_ref.is_killed
                and resolved.sym.shallow_timestamp.cell_num != cell_ctr
            ):
                continue
            did_resolve = True
            if resolved.is_called:
                called_syms.add((resolved, live_symbol_ref.timestamp))
            if resolved.is_live and not resolved.is_unsafe:
                live_symbols.add(resolved)
        if (
            resolved is not None
            and update_liveness_time_versions
            and not live_symbol_ref.is_killed
        ):
            liveness_time = resolved.liveness_timestamp
            resolved.update_usage_info(
                used_time=liveness_time,
                exclude_ns=not resolved.is_last,
                is_static=True,
            )
        if not did_resolve and not live_symbol_ref.is_killed:
            unresolved_live_refs.add(live_symbol_ref)
    (
        live_from_calls,
        live_cells,
        unresolved_from_calls,
    ) = _compute_call_chain_live_symbols_and_cells(
        live_with_stmt_ctr=called_syms,
        cell_ctr=cell_ctr,
        update_liveness_time_versions=update_liveness_time_versions,
    )
    live_symbols |= live_from_calls
    unresolved_live_refs |= unresolved_from_calls
    return live_symbols, live_cells, unresolved_live_refs


def _compute_call_chain_live_symbols_and_cells(
    live_with_stmt_ctr: Set[Tuple[ResolvedSymbol, int]],
    cell_ctr: int,
    update_liveness_time_versions: bool,
) -> Tuple[Set[ResolvedSymbol], Set[int], Set[LiveSymbolRef]]:
    seen: Set[Tuple[ResolvedSymbol, int]] = set()
    worklist: List[Tuple[ResolvedSymbol, int]] = list(live_with_stmt_ctr)
    live: Set[ResolvedSymbol] = set()
    unresolved: Set[LiveSymbolRef] = set()
    while len(worklist) > 0:
        workitem: Tuple[ResolvedSymbol, int] = worklist.pop()
        if workitem in seen:
            continue
        called_sym, stmt_ctr = workitem
        # TODO: handle callable classes
        if called_sym.sym.func_def_stmt is None:
            continue
        seen.add(workitem)
        init_killed = {arg.arg for arg in called_sym.sym.get_definition_args()}
        live_refs, *_ = compute_live_dead_symbol_refs(
            cast(ast.FunctionDef, called_sym.sym.func_def_stmt).body,
            init_killed=init_killed,
            include_killed_live=cell_ctr > 0,
        )
        used_time = Timestamp(cell_ctr, stmt_ctr)
        for symbol_ref in live_refs:
            chain = symbol_ref.ref.chain
            if len(chain) >= 1 and not symbol_ref.is_killed:
                atom = chain[0].value
                did_resolve = isinstance(atom, str) and (
                    atom in init_killed
                    or hasattr(builtins, atom)
                    or atom in _RESOLVER_EXCEPTIONS
                )
            else:
                did_resolve = False
            resolved = None
            for resolved in symbol_ref.gen_resolved_symbols(
                called_sym.sym.call_scope,
                only_yield_final_symbol=False,
                yield_all_intermediate_symbols=True,
            ):
                if (
                    symbol_ref.is_killed
                    and resolved.sym.shallow_timestamp.cell_num != cell_ctr
                ):
                    continue
                # FIXME: kind of hacky
                resolved.atom.is_cascading_reactive = (
                    resolved.atom.is_cascading_reactive
                    or called_sym.is_cascading_reactive
                )
                resolved.atom.is_reactive = (
                    resolved.atom.is_reactive or called_sym.is_reactive
                )
                did_resolve = True
                if resolved.is_called:
                    worklist.append((resolved, stmt_ctr))
                if resolved.sym.is_anonymous:
                    continue
                if resolved.is_live and not resolved.is_unsafe:
                    live.add(resolved)
            if (
                resolved is not None
                and update_liveness_time_versions
                and not symbol_ref.is_killed
            ):
                resolved.update_usage_info(
                    used_time=used_time,
                    exclude_ns=not resolved.is_last,
                    is_static=True,
                )
            if not did_resolve and not symbol_ref.is_killed:
                unresolved.add(symbol_ref)
    return (
        live,
        {called.timestamp.cell_num for called, _ in seen if called.is_live},
        unresolved,
    )


def compute_live_dead_symbol_refs(
    code: Union[ast.AST, List[ast.stmt], str],
    scope: "Scope" = None,
    init_killed: Optional[Set[str]] = None,
    include_killed_live: bool = False,
) -> Tuple[Set[LiveSymbolRef], Set[SymbolRef], Set[SymbolRef]]:
    if init_killed is None:
        init_killed = set()
    if isinstance(code, str):
        code = ast.parse(code)
    elif isinstance(code, list):
        code = ast.Module(code)
    return ComputeLiveSymbolRefs(
        scope=scope, init_killed=init_killed, include_killed_live=include_killed_live
    )(code)


def static_resolve_rvals(
    code: Union[ast.AST, str], cell_ctr: int = -1, scope: Optional["Scope"] = None
) -> Set[ResolvedSymbol]:
    live_refs, *_ = compute_live_dead_symbol_refs(
        code, include_killed_live=cell_ctr > 0
    )
    resolved_live_syms, *_ = get_live_symbols_and_cells_for_references(
        live_refs, scope or flow().global_scope, cell_ctr=cell_ctr
    )
    return resolved_live_syms


def stmt_contains_cascading_reactive_rval(stmt: ast.stmt) -> bool:
    live_refs, *_ = compute_live_dead_symbol_refs(stmt)
    for ref in live_refs:
        for atom in ref.ref.chain:
            if atom.is_cascading_reactive:
                return True
    return False
