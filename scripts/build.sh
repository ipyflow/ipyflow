#!/usr/bin/env bash

mkdir -p ./core/ipyflow/resources/labextension
mkdir -p ./core/ipyflow/resources/nbextension
pushd ./frontend/labextension
yarn install --frozen-lockfile && npm run build:prod
popd
pushd ./frontend/nbextension
yarn install --frozen-lockfile && npm run build
popd
cp ./frontend/labextension/install.json ./nbsafety/resources/labextension
cp ./frontend/nbextension/nbsafety.json ./nbsafety/resources/nbextension
python setup.py sdist bdist_wheel --universal
pushd ./core
python setup.py sdist bdist_wheel --universal
popd
