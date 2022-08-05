# -*- coding: utf-8 -*-
import ast
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Type

from ipyflow.data_model.timestamp import Timestamp
from ipyflow.singletons import flow

if TYPE_CHECKING:
    from ipyflow.data_model.data_symbol import DataSymbol
    from ipyflow.data_model.namespace import Namespace
    from ipyflow.tracing.ipyflow_tracer import ExternalCallArgument


logger = logging.getLogger(__name__)


class ExternalCallHandler:
    def __init__(self, _obj: Any = None, _method_or_function: Any = None) -> None:
        pass

    def process_arg(self, arg: Any) -> None:
        pass

    def _handle_impl(
        self,
        obj_id: int,
        args: List["ExternalCallArgument"],
        stmt_node: ast.stmt,
    ) -> None:
        arg_dsyms: Set["DataSymbol"] = set()
        arg_dsyms = arg_dsyms.union(*(arg[1] for arg in args))
        Timestamp.update_usage_info(arg_dsyms)
        self.handle(obj_id, args, arg_dsyms, stmt_node)

    def _mutate_caller(
        self, obj_id: int, arg_dsyms: Set["DataSymbol"], should_propagate: bool
    ) -> None:
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

    def handle(
        self,
        obj_id: int,
        args: List["ExternalCallArgument"],
        arg_dsyms: Set["DataSymbol"],
        stmt_node: ast.stmt,
    ) -> None:
        self._mutate_caller(
            obj_id,
            arg_dsyms,
            should_propagate=True,
        )


class StandardMutation(ExternalCallHandler):
    pass


class ListMethod(ExternalCallHandler):
    def handle_namespace(
        self,
        namespace: "Namespace",
        args: List["ExternalCallArgument"],
        arg_dsyms: Set["DataSymbol"],
        stmt_node: ast.stmt,
    ) -> None:
        pass

    def handle_mutate_caller(
        self,
        obj_id: int,
        arg_dsyms: Set["DataSymbol"],
    ) -> None:
        pass

    def handle(
        self,
        obj_id: int,
        args: List["ExternalCallArgument"],
        arg_dsyms: Set["DataSymbol"],
        stmt_node: ast.stmt,
    ) -> None:
        mutated_sym = flow().get_first_full_symbol(obj_id)
        if mutated_sym is not None:
            namespace = mutated_sym.namespace
            if namespace is not None:
                self.handle_namespace(namespace, args, arg_dsyms, stmt_node)
        self.handle_mutate_caller(obj_id, arg_dsyms)


class ListExtend(ListMethod):
    def __init__(self, lst: List[Any], *_) -> None:
        self.orig_len = len(lst)

    def handle_namespace(
        self,
        namespace: "Namespace",
        args: List["ExternalCallArgument"],
        arg_dsyms: Set["DataSymbol"],
        stmt_node: ast.stmt,
    ) -> None:
        for upsert_pos in range(self.orig_len, len(namespace.obj)):
            namespace.upsert_data_symbol_for_name(
                upsert_pos,
                namespace.obj[upsert_pos],
                arg_dsyms,
                stmt_node,
                overwrite=False,
                is_subscript=True,
                propagate=False,
            )

    def handle_mutate_caller(
        self,
        obj_id: int,
        arg_dsyms: Set["DataSymbol"],
    ) -> None:
        self._mutate_caller(obj_id, arg_dsyms, should_propagate=False)


class ListAppend(ListExtend):
    def handle_mutate_caller(
        self,
        obj_id: int,
        _arg_dsyms: Set["DataSymbol"],
    ) -> None:
        self._mutate_caller(obj_id, set(), False)


class ListInsert(ListMethod):
    def __init__(self, *_) -> None:
        self.insert_pos: Optional[int] = None

    def handle_namespace(
        self,
        namespace: "Namespace",
        args: List["ExternalCallArgument"],
        _arg_dsyms: Set["DataSymbol"],
        stmt_node: ast.stmt,
    ) -> None:
        if self.insert_pos is None or len(args) < 2:
            return
        inserted_arg_obj, inserted_arg_dsyms = args[1]
        inserted_syms = {
            sym for sym in inserted_arg_dsyms if sym.obj is inserted_arg_obj
        }
        if len(inserted_syms) > 1:
            return
        namespace.shuffle_symbols_upward_from(self.insert_pos)
        namespace.upsert_data_symbol_for_name(
            self.insert_pos,
            namespace.obj[self.insert_pos],
            inserted_syms,
            stmt_node,
            overwrite=False,
            is_subscript=True,
            propagate=True,
        )

    def handle_mutate_caller(
        self,
        obj_id: int,
        _arg_dsyms: Set["DataSymbol"],
    ) -> None:
        self._mutate_caller(obj_id, set(), should_propagate=False)

    def process_arg(self, insert_pos: int) -> None:
        self.insert_pos = insert_pos


class ListRemove(ListMethod):
    def __init__(self, lst: List[Any], *_) -> None:
        self.lst = lst
        self.remove_pos: Optional[int] = None

    def handle_namespace(
        self,
        namespace: "Namespace",
        _args: List["ExternalCallArgument"],
        _arg_dsyms: Set["DataSymbol"],
        _stmt_node: ast.stmt,
    ) -> None:
        if self.remove_pos is None:
            return
        namespace.delete_data_symbol_for_name(self.remove_pos, is_subscript=True)

    def process_arg(self, remove_val: Any) -> None:
        try:
            self.remove_pos = self.lst.index(remove_val)
        except ValueError:
            pass

    def handle_mutate_caller(
        self,
        obj_id: int,
        arg_dsyms: Set["DataSymbol"],
    ) -> None:
        self._mutate_caller(obj_id, arg_dsyms, should_propagate=False)


class ListPop(ListRemove):
    def __init__(self, *_) -> None:
        self.remove_pos: Optional[int] = None

    def process_arg(self, pop_pos: int) -> None:
        self.remove_pos = pop_pos


class NamespaceClear(ExternalCallHandler):
    def handle(
        self,
        obj_id: int,
        _args: List["ExternalCallArgument"],
        _arg_dsyms: Set["DataSymbol"],
        stmt_node: ast.stmt,
    ) -> None:
        mutated_sym = flow().get_first_full_symbol(obj_id)
        if mutated_sym is None:
            return
        namespace = mutated_sym.namespace
        if namespace is None:
            return
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


class MutatingMethodEventNotYetImplemented(ExternalCallHandler):
    pass


class ArgMutate(ExternalCallHandler):
    def handle(
        self,
        obj_id: int,
        _args: List["ExternalCallArgument"],
        arg_dsyms: Set["DataSymbol"],
        stmt_node: ast.stmt,
    ) -> None:
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


def resolve_external_call(
    obj: Optional[Any], function_or_method: Optional[Any], method: Optional[str]
) -> Optional[ExternalCallHandler]:
    if obj is None:
        method_obj = function_or_method
    elif method is None:
        return None
    else:
        method_obj = getattr(type(obj), method, None)
    external_call_type = _METHOD_TO_EVENT_TYPE.get(method_obj, None)
    if external_call_type is None:
        return None
    return external_call_type(obj, method_obj)
