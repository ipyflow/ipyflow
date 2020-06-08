#!/usr/bin/env bash

# TODO: validate non-dirty tree and that current commit has most recent tag
expect <<EOF
set timeout -1

spawn twine upload dist/*

expect "Enter your username:"
send -- "$(lpass show pypi.org --field=username)\r"

expect "Enter your password:"
send -- "$(lpass show pypi.org --field=password)\r"
expect
EOF
pushd ./frontend/labextension
npm publish
popd
