# -*- coding: utf-8 -*-
from enum import Enum


class TraceEvent(Enum):
    line = 'line'
    call = 'call'
    return_ = 'return'
    exception = 'exception'

    # these are included for completeness but will probably not be used
    c_call = 'c_call'
    c_return = 'c_return'
    c_exception = 'c_exception'

    def __str__(self):
        # maxlen = max(len(v.value) for v in self.__class__.__dict__.values() if isinstance(v, self.__class__))
        # return ' ' * (maxlen - len(self.value)) + self.value
        return self.value
