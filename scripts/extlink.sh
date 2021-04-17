#!/usr/bin/env bash

PREFIX="$(python -c 'import sys; print(sys.prefix)')"
pushd "${PREFIX}"/share/jupyter/labextensions
ln -s -f "$(dirs -l -p | tail -1)"/nbsafety/resources/labextension jupyterlab-nbsafety
popd
pushd "${PREFIX}"/share/jupyter/nbextensions
ln -s -f "$(dirs -l -p | tail -1)"/nbsafety/resources/nbextension nbsafety
popd
