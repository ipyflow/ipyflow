from ipyflow.shell import IPyflowInteractiveShell
IPyflowInteractiveShell.instance()

import glob
import pytest
import sys

cov = None
if '--coverage' in sys.argv:
    import coverage
    cov = coverage.Coverage()
    cov.start()

# need PYTHONPATH="." for this to work (when in $ROOT/core)
try:
    import test
except ImportError:
    import sys
    sys.path.append(".")
    import test

if __name__ == "__main__":
    if len(sys.argv) > 1:
        patt = sys.argv[1]
        if not patt.endswith(".py"):
            if patt.startswith("test/"):
                patt = patt[len("test/"):]
            patt = f"test/*{patt}*.py"
        tests = glob.glob(patt)
        sys.argv = [sys.argv[0]] + tests + sys.argv[2:]
    ret = pytest.main()
    if cov is not None:
        cov.stop()
        cov.save()
    sys.exit(ret)
