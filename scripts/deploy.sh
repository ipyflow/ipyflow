#!/usr/bin/env bash

# ref: https://vaneyckt.io/posts/safer_bash_scripts_with_set_euxo_pipefail/
set -euxo pipefail

./scripts/verify-git-version.sh > /dev/null

pushd $1
expect <<EOF
set timeout -1

spawn twine upload dist/*

expect "Enter your API token:"
send -- "$(lpass show 937494930560669633 --password)\r"
expect
EOF
popd

git push --tags
