# -*- coding: utf-8 -*-
import sys

if sys.version_info >= (3, 7):
    from . import ast_helper as fast
else:
    from .ast_helper import FastAst as fast
