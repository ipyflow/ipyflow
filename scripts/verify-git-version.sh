#!/usr/bin/env bash

# ref: https://vaneyckt.io/posts/safer_bash_scripts_with_set_euxo_pipefail/
set -euxo pipefail

if [[ -n $(git status -s) ]]; then
    echo "dirty working tree; please clean or commit changes"
    exit 1
fi

if ! git describe --exact-match --tags HEAD > /dev/null; then
    echo "current revision not tagged; please deploy from a tagged revision"
    exit 1
fi

current="$(python -c 'import versioneer; print(versioneer.get_version())')"
[[ $? -eq 1 ]] && exit 1

latest="$(git describe --tags $(git rev-list --tags --max-count=1))"
[[ $? -eq 1 ]] && exit 1

if [[ "$current" != "$latest" ]]; then
    echo "current revision is not the latest version; please deploy from latest version"
    exit 1
fi

echo "$current"

