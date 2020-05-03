#!/usr/bin/env bash

# ref: https://github.com/ipython/ipython/issues/9752
PYTHONPATH="." ipython3 --quick --no-banner --quiet --colors=NoColor --simple-prompt test_runner.py -- $@
