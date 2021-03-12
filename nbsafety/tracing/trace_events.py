# -*- coding: future_annotations -*-
from enum import Enum

from nbsafety.utils import fast


EMIT_EVENT = '_X5ix_NBSAFETY_EVT_EMIT'


class TraceEvent(Enum):
    # handlers for `all` are triggered on every event
    all_ = 'all'

    before_stmt = 'before_stmt'
    after_stmt = 'after_stmt'

    attribute = 'attribute'
    subscript = 'subscript'

    before_complex_symbol = 'before_complex_symbol'
    after_complex_symbol = 'after_complex_symbol'

    before_arg_list = 'before_arg_list'
    after_arg_list = 'after_arg_list'
    argument = 'argument'

    before_literal = 'before_literal'
    after_literal = 'after_literal'
    dict_key = 'dict_key'
    dict_value = 'dict_value'
    list_elt = 'list_elt'
    tuple_elt = 'tuple_elt'

    before_return = 'before_return'

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
