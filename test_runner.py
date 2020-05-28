# -*- coding: utf-8 -*-

# need PYTHONPATH="." for this to work
import builtins
try:
    import test
except ImportError:
    import sys
    sys.path.append(".")
    import test

if __name__ == '__main__':
    setattr(builtins, '__exit_zero', True)
    for name, mod in test.__dict__.items():
        # weird; need to reimport these for some reason
        import sys
        import ipytest
        if name.startswith('test_') and isinstance(mod, type(sys)):
            if ipytest.run(*sys.argv[1:], filename=mod.__file__, return_exit_code=True) != 0:
                import builtins
                setattr(builtins, '__exit_zero', False)
    # Totally bizarre; we lose all our variables
    # Extreme hack to keep the zero exit status around
    import builtins
    sys.exit(0) if getattr(builtins, '__exit_zero') else sys.exit(1)
