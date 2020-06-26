#!/usr/bin/env bash

if [ -z "$1" ]; then
    prefix="--sys-prefix"
else
    prefix="$1"
fi

source "$HOME/.virtualenvs/nbsafety/bin/activate"
pushd ./frontend/nbextension
npm run build
popd
jupyter nbextension install --py nbsafety "$prefix"
jupyter nbextension enable --py nbsafety "$prefix"
