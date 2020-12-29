# -*- coding: utf-8 -*-
import ast
from contextlib import contextmanager
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Optional

_LOCATION_OF_NODE: 'Optional[ast.AST]' = None


class FastAst(object):
    @classmethod
    @contextmanager
    def location_of(cls, node):
        global _LOCATION_OF_NODE
        old_location_of_node = _LOCATION_OF_NODE
        _LOCATION_OF_NODE = node
        yield
        _LOCATION_OF_NODE = old_location_of_node


def _make_ctor(ctor_name):
    def ctor(*args, **kwargs):
        ret = getattr(ast, ctor_name)(*args, **kwargs)
        if _LOCATION_OF_NODE is not None:
            ast.copy_location(ret, _LOCATION_OF_NODE)
        return ret
    return ctor


for ctor_name in ast.__dict__:
    if ctor_name.startswith('_'):
        continue
    setattr(FastAst, ctor_name, staticmethod(_make_ctor(ctor_name)))

if sys.version_info >= (3, 9):
    FastAst.Str = staticmethod(_make_ctor('Constant'))
    FastAst.Num = staticmethod(_make_ctor('Constant'))


def __getattr__(name: str) -> 'Any':
    return getattr(FastAst, name)
