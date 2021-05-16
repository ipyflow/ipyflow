# -*- coding: utf-8 -*-

import builtins
import sys

if '--coverage' in sys.argv:
    import coverage
    cov = coverage.Coverage()
    cov.start()
    setattr(builtins, '__codecov', cov)
    sys.argv.remove('--coverage')

# need PYTHONPATH="." for this to work
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
    import sys
    import builtins
    cov = getattr(builtins, '__codecov', None)
    if cov is not None:
        cov.stop()
        cov.save()
    sys.exit(0) if getattr(builtins, '__exit_zero') else sys.exit(1)
