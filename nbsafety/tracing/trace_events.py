# -*- coding: utf-8 -*-
from enum import Enum

from nbsafety.utils import fast


EMIT_EVENT = '_X5ix_NBSAFETY_EVT_EMIT'


class TraceEvent(Enum):
    after_stmt = 'after_stmt'
    after_attrsub_chain = 'after_attrsub_chain'

    line = 'line'
    call = 'call'
    return_ = 'return'
    exception = 'exception'

    # these are included for completeness but will probably not be used
    c_call = 'c_call'
    c_return = 'c_return'
    c_exception = 'c_exception'

    def __str__(self):
        return self.value

    def to_ast(self):
        return fast.Constant(self.value)
