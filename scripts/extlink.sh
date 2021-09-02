#!/usr/bin/env bash

PREFIX="$(python -c 'import sys; print(sys.prefix)')"
mkdir -p "${PREFIX}"/share/jupyter/labextensions
pushd "${PREFIX}"/share/jupyter/labextensions
if [[ -d jupyterlab-nbsafety ]]; then
    rm -rf jupyterlab-nbsafety
fi
ln -s -f "$(dirs -l -p | tail -1)"/nbsafety/resources/labextension jupyterlab-nbsafety
popd
mkdir -p "${PREFIX}"/share/jupyter/nbextensions
pushd "${PREFIX}"/share/jupyter/nbextensions
if [[ -d nbsafety ]]; then
    rm -rf nbsafety
fi
ln -s -f "$(dirs -l -p | tail -1)"/nbsafety/resources/nbextension nbsafety
popd
