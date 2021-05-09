# -*- coding: future_annotations -*-

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
    pass


class ArgMutate(MutationEvent):
    pass

