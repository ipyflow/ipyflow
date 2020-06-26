# -*- coding: utf-8 -*-
.PHONY: clean build deploy check test tests deps devdeps typecheck checkall testall uitest version bump markdown kernel nbext

clean:
	rm -rf build/ dist/ nbsafety.egg-info/

build: clean version
	./scripts/build.sh

version:
	./scripts/build-version.py

bump:
	./scripts/build-version.py --bump --tag

markdown:
	# ref: https://github.com/andreasbm/readme
	npx @appnest/readme generate -i markdown-blueprints/README.md -o README.md
	npx @appnest/readme generate -i markdown-blueprints/CONTRIBUTORS.md -o CONTRIBUTORS.md

deploy: build
	./scripts/deploy.sh

check:
	./scripts/runtests.sh

uicheck:
	./scripts/runtests.sh ui

checkall: check uicheck

test: check
uitest: uicheck
tests: check
testall: checkall

deps:
	pip install -r requirements.txt

devdeps:
	pip install -e .
	pip install -r requirements-dev.txt

kernel:
	python -m nbsafety.install --sys-prefix

nbext:
	./scripts/nbext.sh --sys-prefix

typecheck:
	find nbsafety -iname '*.py' -print0 | xargs -0 mypy
