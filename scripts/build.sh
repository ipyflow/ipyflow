#!/usr/bin/env bash

pushd ./frontend/labextension
npm run build
popd
pushd ./frontend/nbextension
npm run build
popd
python setup.py sdist bdist_wheel --universal
