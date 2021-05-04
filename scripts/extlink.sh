#!/usr/bin/env bash

PREFIX="$(python -c 'import sys; print(sys.prefix)')"
mkdir -p "${PREFIX}"/share/jupyter/labextensions
pushd "${PREFIX}"/share/jupyter/labextensions
ln -s -f "$(dirs -l -p | tail -1)"/nbsafety/resources/labextension jupyterlab-nbsafety
popd
pushd "${PREFIX}"/share/jupyter/nbextensions
mkdir -p "${PREFIX}"/share/jupyter/nbextensions
ln -s -f "$(dirs -l -p | tail -1)"/nbsafety/resources/nbextension nbsafety
popd
