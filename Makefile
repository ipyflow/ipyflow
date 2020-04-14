.PHONY: test kernel

test:
	ipython3 test_project.py

kernel:
    python -m safe_kernel.install