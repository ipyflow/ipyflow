# -*- coding: utf-8 -*-
import ast
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Type

from ipyflow.data_model.timestamp import Timestamp
from ipyflow.singletons import flow

if TYPE_CHECKING:
    from ipyflow.data_model.data_symbol import DataSymbol


logger = logging.getLogger(__name__)


class ExternalCallHandler:
    def __init__(self, _obj: Any = None, _method_or_function: Any = None) -> None:
        pass

    def process_arg(self, arg: Any) -> None:
        pass

    def handle(
        self,
        obj_id: int,
        arg_dsyms: Set["DataSymbol"],
        arg_objs: List[Any],
        stmt_node: ast.stmt,
    ) -> None:
        Timestamp.update_usage_info(arg_dsyms)
        # NOTE: this next block is necessary to ensure that we add the argument as a namespace child
        # of the mutated symbol. This helps to avoid propagating through to dependency children that are
        # themselves namespace children.
        should_propagate = True
        if not isinstance(
            self,
            (MutatingMethodEventNotYetImplemented, StandardMutation),
        ):
            should_propagate = False
            mutation_upsert_deps: Set["DataSymbol"] = set()
            if isinstance(self, (ListAppend, ListInsert)):
                dsyms, mutation_upsert_deps = (
                    mutation_upsert_deps,
                    arg_dsyms,
                )
            self._handle_specific_mutation_type(obj_id, mutation_upsert_deps, stmt_node)
        mutated_syms = flow().aliases.get(obj_id, set())
        Timestamp.update_usage_info(mutated_syms)
        for mutated_sym in mutated_syms:
            mutated_sym.update_deps(
                arg_dsyms,
                overwrite=False,
                mutated=True,
                propagate_to_namespace_descendents=should_propagate,
                refresh=should_propagate,
            )

    def _handle_specific_mutation_type(
        self,
        mutated_obj_id: int,
        mutation_upsert_deps: Set["DataSymbol"],
        stmt_node: ast.stmt,
    ) -> None:
        mutated_sym = flow().get_first_full_symbol(mutated_obj_id)
        if mutated_sym is None:
            return
        namespace = mutated_sym.namespace
        if namespace is None:
            return
        mutated_obj = mutated_sym.obj
        if isinstance(self, (ListAppend, ListExtend)):
            for upsert_pos in range(
                self.orig_len if isinstance(self, ListExtend) else len(mutated_obj) - 1,
                len(mutated_obj),
            ):
                logger.info(
                    "upsert %s to %s with deps %s",
                    len(mutated_obj) - 1,
                    namespace,
                    mutation_upsert_deps,
                )
                namespace.upsert_data_symbol_for_name(
                    upsert_pos,
                    mutated_obj[upsert_pos],
                    mutation_upsert_deps,
                    stmt_node,
                    overwrite=False,
                    is_subscript=True,
                    propagate=False,
                )
        elif isinstance(self, ListInsert):
            assert mutated_obj is namespace.obj
            namespace.shuffle_symbols_upward_from(self.insert_pos)
            namespace.upsert_data_symbol_for_name(
                self.insert_pos,
                mutated_obj[self.insert_pos],
                mutation_upsert_deps,
                stmt_node,
                overwrite=False,
                is_subscript=True,
                propagate=True,
            )
        elif isinstance(self, (ListPop, ListRemove)) and self.remove_pos is not None:
            assert mutated_obj is namespace.obj
            namespace.delete_data_symbol_for_name(self.remove_pos, is_subscript=True)
        elif isinstance(self, NamespaceClear):
            for name in sorted(
                (
                    dsym.name
                    for dsym in namespace.all_data_symbols_this_indentation(
                        exclude_class=True, is_subscript=True
                    )
                ),
                reverse=True,
            ):
                namespace.delete_data_symbol_for_name(name, is_subscript=True)


class StandardMutation(ExternalCallHandler):
    pass


class ListAppend(ExternalCallHandler):
    pass


class ListExtend(ExternalCallHandler):
    def __init__(self, lst: List[Any], *_) -> None:
        self.orig_len = len(lst)


class ListInsert(ExternalCallHandler):
    def __init__(self, *_) -> None:
        self.insert_pos: Optional[int] = None

    def process_arg(self, insert_pos: int) -> None:
        self.insert_pos = insert_pos


class ListRemove(ExternalCallHandler):
    def __init__(self, lst: List[Any], *_) -> None:
        self.lst = lst
        self.remove_pos: Optional[int] = None

    def process_arg(self, remove_val: Any) -> None:
        try:
            self.remove_pos = self.lst.index(remove_val)
        except ValueError:
            pass


class ListPop(ExternalCallHandler):
    def __init__(self, *_) -> None:
        self.remove_pos: Optional[int] = None

    def process_arg(self, pop_pos: int) -> None:
        self.remove_pos = pop_pos


class NamespaceClear(ExternalCallHandler):
    pass


class MutatingMethodEventNotYetImplemented(ExternalCallHandler):
    pass


class ArgMutate(ExternalCallHandler):
    def handle(
        self,
        obj_id: int,
        arg_dsyms: Set["DataSymbol"],
        arg_objs: List[Any],
        stmt_node: ast.stmt,
    ) -> None:
        Timestamp.update_usage_info(arg_dsyms)
        for mutated_sym in arg_dsyms:
            if mutated_sym is None or mutated_sym.is_anonymous:
                continue
            # TODO: happens when module mutates args
            #  should we add module as a dep in this case?
            mutated_sym.update_deps(set(), overwrite=False, mutated=True)


_METHOD_TO_EVENT_TYPE: Dict[Any, Type[ExternalCallHandler]] = {
    list.append: ListAppend,
    list.clear: NamespaceClear,
    list.extend: ListExtend,
    list.insert: ListInsert,
    list.pop: ListPop,
    list.remove: ListRemove,
    list.sort: MutatingMethodEventNotYetImplemented,
    dict.clear: NamespaceClear,
    dict.pop: MutatingMethodEventNotYetImplemented,
    dict.popitem: MutatingMethodEventNotYetImplemented,
    dict.setdefault: MutatingMethodEventNotYetImplemented,
    dict.update: MutatingMethodEventNotYetImplemented,
    set.clear: MutatingMethodEventNotYetImplemented,
    set.difference_update: MutatingMethodEventNotYetImplemented,
    set.discard: MutatingMethodEventNotYetImplemented,
    set.intersection_update: MutatingMethodEventNotYetImplemented,
    set.pop: MutatingMethodEventNotYetImplemented,
    set.remove: MutatingMethodEventNotYetImplemented,
    set.symmetric_difference_update: MutatingMethodEventNotYetImplemented,
    set.update: MutatingMethodEventNotYetImplemented,
}


def resolve_mutating_method(
    obj: Any, method: Optional[str]
) -> Optional[ExternalCallHandler]:
    if method is None:
        return None
    method_obj = getattr(type(obj), method, None)
    mutation_type = _METHOD_TO_EVENT_TYPE.get(method_obj, None)
    if mutation_type is None:
        return None
    return mutation_type(obj, method_obj)
