# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING, Any, Optional

from ipyflow.singletons import flow
from ipyflow.tracing.external_calls.base_handlers import ExternalCallHandler

if TYPE_CHECKING:
    from ipyflow.data_model.namespace import Namespace


class ListMethod(ExternalCallHandler):
    def handle_namespace(self, namespace: "Namespace") -> None:
        pass

    def handle(self) -> None:
        caller_self_obj_id = self.caller_self_obj_id
        mutated_sym = flow().get_first_full_symbol(caller_self_obj_id)
        if mutated_sym is not None:
            namespace = mutated_sym.namespace
            if namespace is not None:
                self.handle_namespace(namespace)
        self.mutate_caller(should_propagate=False)


class ListExtend(ListMethod):
    orig_len: int = None

    def initialize(self, **kwargs) -> None:
        self.orig_len = len(self.caller_self)

    def handle_namespace(self, namespace: "Namespace") -> None:
        for upsert_pos in range(self.orig_len, len(namespace.obj)):
            namespace.upsert_data_symbol_for_name(
                upsert_pos,
                namespace.obj[upsert_pos],
                self.arg_dsyms,
                overwrite=False,
                is_subscript=True,
                propagate=False,
            )


class ListAppend(ListExtend):
    pass


class ListInsert(ListMethod):
    insert_pos: Optional[int] = None

    def handle_namespace(self, namespace: "Namespace") -> None:
        if self.insert_pos is None or len(self.args) < 2:
            return
        inserted_arg_obj, inserted_arg_dsyms = self.args[1]
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
            self.stmt_node,
            overwrite=False,
            is_subscript=True,
            propagate=True,
        )

    def process_arg(self, insert_pos: int) -> None:
        self.insert_pos = insert_pos


class ListRemove(ListMethod):
    remove_pos: Optional[int] = None

    def handle_namespace(self, namespace: "Namespace") -> None:
        if self.remove_pos is None:
            return
        namespace.delete_data_symbol_for_name(self.remove_pos, is_subscript=True)

    def process_arg(self, remove_val: Any) -> None:
        try:
            self.remove_pos = self.caller_self.index(remove_val)
        except ValueError:
            pass


class ListPop(ListRemove):
    def process_arg(self, pop_pos: int) -> None:
        self.remove_pos = pop_pos
