#!/usr/bin/env bash

GRC=""
if [ -x "$(command -v grc)" ]; then
    # -s and -e sends both stdout and stderr to grcat
    GRC="grc --config=./tests/grc.conf -s -e"
fi
$GRC ipython3 ./tests/test_dependencies.py
