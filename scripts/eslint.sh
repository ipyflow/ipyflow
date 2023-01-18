#!/usr/bin/env bash

set -e  # just use set -e while we source nvm
source $HOME/.nvm/nvm.sh;
nvm use default

# ref: https://vaneyckt.io/posts/safer_bash_scripts_with_set_euxo_pipefail/
set -euxo pipefail

pushd frontend/labextension
npm run eslint:check
popd

pushd frontend/nbextension
npm run eslint:check
popd
