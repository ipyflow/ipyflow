# -*- coding: future_annotations -*-
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Optional


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
        self.insert_pos = insert_pos


class ArgMutate(MutationEvent):
    pass

