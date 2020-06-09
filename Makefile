# -*- coding: utf-8 -*-
.PHONY: clean build deploy check test tests deps devdeps typecheck checkall testall

clean:
	rm -rf build/ dist/ nbsafety.egg-info/

build: clean
	./scripts/build.sh

# TODO: check for dirty tree here
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
