#!/usr/bin/env bash

PREFIX="$(python -c 'import sys; print(sys.prefix)')"
mkdir -p "${PREFIX}"/share/jupyter/labextensions
pushd "${PREFIX}"/share/jupyter/labextensions
if [[ -d jupyterlab-ipyflow ]]; then
    rm -rf jupyterlab-ipyflow
fi
ln -s -f "$(dirs -l -p | tail -1)"/core/ipyflow/resources/labextension jupyterlab-ipyflow
popd

