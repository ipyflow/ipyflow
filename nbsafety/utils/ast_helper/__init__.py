# -*- coding: utf-8 -*-
import sys

if sys.version_info >= (3, 7):
    from . import ast_helper as fast
else:
    from typing import cast
    from . import ast_helper
    fast = cast(type(ast_helper), ast_helper.FastAst)
