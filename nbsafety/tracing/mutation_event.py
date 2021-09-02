# -*- coding: future_annotations -*-
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional


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


class ArgMutate(MutationEvent):
    pass

