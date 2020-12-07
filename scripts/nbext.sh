#!/usr/bin/env bash

if [ -z "$1" ]; then
    prefix="--sys-prefix"
else
    prefix="$1"
fi

pushd ./frontend/nbextension
npm install
npm run build
popd
jupyter nbextension install --py nbsafety "$prefix"
jupyter nbextension enable --py nbsafety "$prefix"
