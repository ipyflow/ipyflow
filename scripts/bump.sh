#!/usr/bin/env bash

./scripts/build-version.py --bump --tag
git tag -d $(python -c 'from nbsafety.version import version; print(version)')
git add -u .
git commit -m "bump version"
./scripts/build-version.py --bump --tag
