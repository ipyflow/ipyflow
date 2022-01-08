# -*- coding: utf-8 -*-
from typing import Any, Dict, Optional, Type


class MutationEvent:
    pass


class StandardMutation(MutationEvent):
    pass


class ListAppend(MutationEvent):
    pass


class ListExtend(MutationEvent):
    def __init__(self, orig_len: int) -> None:
        super().__init__()
        self.orig_len: int = orig_len


class ListInsert(MutationEvent):
    def __init__(self, insert_pos: Optional[int] = None):
        super().__init__()
        self.pos = insert_pos


class ListRemove(MutationEvent):
    def __init__(self, remove_pos: Optional[int] = None):
        super().__init__()
        self.pos = remove_pos


class ListPop(MutationEvent):
    def __init__(self, pop_pos: Optional[int] = None):
        super().__init__()
        self.pos = pop_pos


class NamespaceClear(MutationEvent):
    pass


class MutatingMethodEventNotYetImplemented(MutationEvent):
    pass


class ArgMutate(MutationEvent):
    pass


_METHOD_TO_EVENT_TYPE: Dict[Any, Type[MutationEvent]] = {
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


def resolve_mutating_method(obj: Any, method: Optional[str]) -> Optional[MutationEvent]:
    if method is None:
        return None
    mutation_type = _METHOD_TO_EVENT_TYPE.get(getattr(type(obj), method, None), None)
    if mutation_type is None:
        return None
    if mutation_type is ListExtend:
        return ListExtend(len(obj))
    else:
        return mutation_type()
