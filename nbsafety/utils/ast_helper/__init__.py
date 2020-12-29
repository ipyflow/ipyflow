# -*- coding: utf-8 -*-
import sys

if sys.version_info >= (3, 7):
    from . import ast_helper as fast
else:
    from typing import cast
    from . import ast_helper
    from .ast_helper import FastAst as fast
    fast = cast(type(ast_helper), fast)
