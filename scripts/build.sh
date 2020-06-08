#!/usr/bin/env bash

python setup.py sdist bdist_wheel --universal
pushd ./frontend/labextension
npm run build
popd
