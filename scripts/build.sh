#!/usr/bin/env bash

mkdir -p ./nbsafety/resources/labextension
mkdir -p ./nbsafety/resources/nbextension
pushd ./frontend/labextension
yarn install --frozen-lockfile && npm run build:prod
popd
pushd ./frontend/nbextension
yarn install --frozen-lockfile && npm run build
popd
cp ./frontend/labextension/install.json ./nbsafety/resources/labextension
cp ./frontend/nbextension/nbsafety.json ./nbsafety/resources/nbextension
python setup.py sdist bdist_wheel --universal
