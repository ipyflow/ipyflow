# -*- coding: utf-8 -*-
from __future__ import annotations
import sys


if __name__ == '__main__':
    import test_dependencies
    import test_hyperedge
    import test_lineno_stmt_map
    modulenames = set(sys.modules) & set(globals())
    test_modules = [sys.modules[name] for name in modulenames if name.startswith('test_')]
    for mod in test_modules:
        # weird; need to reimport these for some reason
        import sys
        import ipytest
        ipytest.run(*sys.argv[1:], filename=mod.__file__)
