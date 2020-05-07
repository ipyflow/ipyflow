# -*- coding: utf-8 -*-
import sys

from .kernel.install import main


# this is just a pointer to nbsafety.kernel.install
if __name__ == '__main__':
    sys.exit(main())
