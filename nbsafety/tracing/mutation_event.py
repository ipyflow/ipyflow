# -*- coding: utf-8 -*-
from enum import Enum


class MutationEvent(Enum):
    normal = 'normal'
    list_append = 'list_append'
    arg_mutate = 'argument_mutation'
