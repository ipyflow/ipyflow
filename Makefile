# -*- coding: utf-8 -*-
.PHONY: clean build deploy check test tests deps devdeps typecheck checkall testall version bump

clean:
	rm -rf build/ dist/ nbsafety.egg-info/ frontend/labextension/package.json

build: clean version
	./scripts/build.sh

version:
	./scripts/build-version.py

bump:
	./scripts/build-version.py --bump --tag

deploy: build
	./scripts/deploy.sh

check:
	./scripts/runtests.sh

checkall:
	SHOULD_SKIP_KNOWN_FAILING=0 ./scripts/runtests.sh

test: check
tests: check
testall: checkall

deps:
	pip install -r requirements.txt

devdeps:
	pip install -e .
	pip install -r requirements-dev.txt

kernel:
	python -m nbsafety.install

typecheck:
	find nbsafety -iname '*.py' -print0 | xargs -0 mypy
