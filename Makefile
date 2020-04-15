.PHONY: test deps devdeps dev

test:
	./runtests.sh

deps:
	pip install -r requirements.txt

devdeps:
	pip install -e .
	pip install -r requirements-dev.txt

kernel:
	python -m nbsafety.safe_kernel.install

dev: kernel
