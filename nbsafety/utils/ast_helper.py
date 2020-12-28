# -*- coding: utf-8 -*-
import ast
from contextlib import contextmanager
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


for ctor_name in ast.__dict__:
    if ctor_name.startswith('_'):
        continue

    def _make_ctor(ctor_name):
        def ctor(cls, *args, **kwargs):
            ret = getattr(ast, ctor_name)(*args, **kwargs)
            if _LOCATION_OF_NODE is not None:
                ast.copy_location(ret, _LOCATION_OF_NODE)
            return ret
        return ctor
    setattr(FastAst, ctor_name, classmethod(_make_ctor(ctor_name)))


def __getattr__(name: str) -> 'Any':
    return getattr(FastAst, name)
