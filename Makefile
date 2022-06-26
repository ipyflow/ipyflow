# -*- coding: utf-8 -*-
.PHONY: clean black blackcheck imports build deploy check check_no_typing test tests deps devdeps dev typecheck version bump extlink kernel nbext

clean:
	rm -rf __pycache__ core/__pycache__ build/ core/build/ core/dist/ dist/ ipyflow.egg-info/ core/ipyflow_core.egg-info core/ipyflow/resources/nbextension core/ipyflow/resources/labextension

build: clean
	./scripts/build.sh

version:
	./scripts/build-version.py

bump:
	./scripts/bump.sh

deploy: version build
	./scripts/deploy.sh

black:
	isort ./core
	./scripts/blacken.sh

blackcheck:
	isort ./core --check-only
	./scripts/blacken.sh --check

imports:
	pycln ./core
	isort ./core

typecheck:
	./scripts/typecheck.sh

# this is the one used for CI, since sometimes we want to skip typcheck
check_no_typing:
	./scripts/runtests.sh

coverage:
	rm -f .coverage
	rm -rf htmlcov
	./scripts/runtests.sh --coverage
	mv core/.coverage .
	coverage html
	coverage report

xmlcov: coverage
	coverage xml

check: blackcheck typecheck check_no_typing

test: check
tests: check

deps:
	pip install -r requirements.txt

devdeps:
	pip install -e .
	pip install -e .[dev]

extlink:
	./scripts/extlink.sh

kernel:
	python -m ipyflow.install --sys-prefix

nbext:
	./scripts/nbext.sh --sys-prefix

dev: devdeps build extlink kernel nbext
