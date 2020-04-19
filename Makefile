# -*- coding: utf-8 -*-
.PHONY: check test tests deps devdeps typecheck

check:
	./runtests.sh

test: check
tests: check

deps:
	pip install -r requirements.txt

devdeps:
	pip install -e .
	pip install -r requirements-dev.txt

kernel:
	python -m nbsafety.safe_kernel.install

typecheck:
	mypy --no-strict-optional --ignore-missing-import nbsafety/safety.py
