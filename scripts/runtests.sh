#!/usr/bin/env bash

set -e

# ref: https://github.com/ipython/ipython/issues/9752

if [ "$1" == "ui" ]; then
    pushd ./frontend/test
    ./run_tests.py
    popd
else
    pushd core
    env PYTHONPATH="." ipython3 --quick --no-banner --quiet --colors=NoColor --simple-prompt ../scripts/test_runner.py -- $@
    popd
fi
