#!/usr/bin/env bash

./scripts/build-version.py --bump --tag
git tag -d $(python -c 'from ipyflow.version import version; print(version)')
git add -u .
git commit -m "[no ci] bump version"
./scripts/build-version.py --bump --tag
