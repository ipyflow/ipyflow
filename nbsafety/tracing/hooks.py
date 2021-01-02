# -*- coding: utf-8 -*-
from enum import Enum


class TracingHook(Enum):
    # TODO: make this 1-1 with TraceEvents and unify
    attrsub_tracer = '_NBSAFETY_ATTR_TRACER'
    end_tracer = '_NBSAFETY_ATTR_TRACER_END'
    arg_recorder = '_NBSAFETY_ARG_RECORDER'
    scope_pusher = '_NBSAFETY_SCOPE_PUSHER'
    scope_popper = '_NBSAFETY_SCOPE_POPPER'
    literal_tracer = '_NBSAFETY_LITERAL_TRACER'
    before_stmt_tracer = '_NBSAFETY_BEFORE_STMT_TRACER'
    after_stmt_tracer = '_NBSAFETY_AFTER_STMT_TRACER'
