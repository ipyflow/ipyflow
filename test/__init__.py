# -*- coding: utf-8 -*-
import glob
import importlib
import os


pkg = os.path.basename(os.path.dirname(__file__))
for mod in glob.glob(os.path.join(os.path.dirname(__file__), "test_*.py")):
    mod = os.path.basename(mod)[:-3]
    importlib.import_module(pkg + '.' + mod, '.')
