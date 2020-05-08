# -*- coding: utf-8 -*-
.PHONY: clean deploy check test tests deps devdeps typecheck checkall testall

clean:
	rm -rf build/ dist/ nbsafety.egg-info/

deploy: clean
	python setup.py sdist bdist_wheel --universal
	twine upload dist/*

check:
	./runtests.sh

checkall:
	SHOULD_SKIP_KNOWN_FAILING=0 ./runtests.sh

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
	mypy --no-strict-optional --ignore-missing-import nbsafety/safety.py
