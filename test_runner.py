# -*- coding: utf-8 -*-

# need PYTHONPATH="." for this to work
import test

if __name__ == '__main__':
    for name, mod in test.__dict__.items():
        # weird; need to reimport these for some reason
        import sys
        import ipytest
        if name.startswith('test_') and isinstance(mod, type(sys)):
            ipytest.run(*sys.argv[1:], filename=mod.__file__)
