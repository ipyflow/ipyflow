import sys

import ipytest


if __name__ == '__main__':
    import test_dependencies
    modulenames = set(sys.modules) & set(globals())
    test_modules = [sys.modules[name] for name in modulenames if name.startswith('test_')]
    for mod in test_modules:
        ipytest.run(*sys.argv[1:], filename=mod.__file__)
