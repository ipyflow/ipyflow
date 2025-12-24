#!/usr/bin/env bash

set -e  # just use set -e while we source nvm
source $HOME/.nvm/nvm.sh;
nvm use default

# ref: https://vaneyckt.io/posts/safer_bash_scripts_with_set_euxo_pipefail/
set -euxo pipefail

current="$(./scripts/verify-git-version.sh)"

jlab="$(python -c 'import json; print(json.loads(open("frontend/labextension/package.json").read())["version"])')"
[[ $? -eq 1 ]] && exit 1

if [[ "$current" != "$jlab" ]]; then
    echo "current revision is not the latest version; please deploy from latest version"
    exit 1
fi

for pkg_dir in ./core .; do
    ./scripts/deploy.sh $pkg_dir
    popd
done

pushd ./frontend/labextension
npm publish
git restore ../../core/ipyflow/resources
popd

git push --tags
