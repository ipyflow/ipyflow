#!/usr/bin/env bash

GRC=""
if [ -x "$(command -v grc)" ]; then
    GRC="grc --config=./tests/grc.conf"
fi
$GRC ./tests/test-wrapper.sh ./tests/test_project.py
