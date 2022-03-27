#!/usr/bin/env bash

mkdir -p ./core/ipyflow/resources/labextension
mkdir -p ./core/ipyflow/resources/nbextension
pushd ./frontend/labextension
yarn install --frozen-lockfile && npm run build:prod
popd
pushd ./frontend/nbextension
yarn install --frozen-lockfile && npm run build
popd
cp ./frontend/labextension/install.json ./core/ipyflow/resources/labextension
cp ./frontend/nbextension/ipyflow.json ./core/ipyflow/resources/nbextension
python setup.py sdist bdist_wheel --universal
pushd ./core
python setup.py sdist bdist_wheel --universal
popd
