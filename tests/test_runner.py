# -*- coding: utf-8 -*-
from __future__ import annotations
import sys


if __name__ == '__main__':
    import test_dep_integration
    import test_lineno_stmt_map
    import test_stmt_edges
    modulenames = set(sys.modules) & set(globals())
    test_modules = [sys.modules[name] for name in modulenames if name.startswith('test_')]
    for mod in test_modules:
        # weird; need to reimport these for some reason
        import sys
        import ipytest
        ipytest.run(*sys.argv[1:], filename=mod.__file__)
