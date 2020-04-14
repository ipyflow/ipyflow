.PHONY: test deps devdeps dev

test:
	ipython tests/test_project.py

deps:
	pip install -r requirements.txt

devdeps:
	pip install -e .
	pip install -r requirements-dev.txt

kernel: deps devdeps
	python -m nbsafety.safe_kernel.install

dev: kernel
