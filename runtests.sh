#!/usr/bin/env bash

# ref: https://github.com/ipython/ipython/issues/9752
ipython3 --quick --no-banner --quiet --colors=NoColor --simple-prompt tests/test_runner.py -- $@
