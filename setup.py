#!/usr/bin/env python
# -*- coding: utf-8 -*-
from glob import glob
from setuptools import setup

import versioneer

pkg_name = "ipyflow"


def read_file(fname):
    with open(fname, "r", encoding="utf8") as f:
        return f.read()


history = read_file("HISTORY.rst")
requirements = read_file("requirements.txt").strip().split()

setup(
    name=pkg_name,
    version=versioneer.get_version(),
    cmdclass=versioneer.get_cmdclass(),
    author="Stephen Macke",
    author_email="stephen.macke@gmail.com",
    description="A reactive Python kernel for Jupyter notebooks.",
    long_description=read_file("README.md"),
    long_description_content_type="text/markdown",
    url="https://github.com/ipyflow/ipyflow",
    packages=[],
    include_package_data=True,
    data_files=[
        ("share/jupyter/labextensions/jupyterlab-ipyflow",
            glob("core/ipyflow/resources/labextension/package.json")
        ),
        ("share/jupyter/labextensions/jupyterlab-ipyflow/static",
            glob("core/ipyflow/resources/labextension/static/*")
        ),
        # like `python -m ipyflow.install --sys-prefix`
        ("share/jupyter/kernels/ipyflow", [
            "core/ipyflow/resources/kernel/kernel.json",
            "core/ipyflow/resources/kernel/logo-32x32.png",
            "core/ipyflow/resources/kernel/logo-64x64.png",
        ]),
        # like jupyter server extension enable ipyflow --sys-prefix
        # (multiple cases to handle different versions)
        ("etc/jupyter/jupyter_notebook_config.d", [
            "core/ipyflow/resources/jupyter_notebook_config.d/ipyflow.json",
        ]),
        ("etc/jupyter/jupyter_server_config.d", [
            "core/ipyflow/resources/jupyter_server_config.d/ipyflow.json",
        ]),
    ],
    install_requires=requirements,
    extras_require={
        "typecheck": ["ipyflow-core[typecheck]"],
        "test": ["ipyflow-core[test]"],
        "dev": ["ipyflow-core[dev]"],
    },
    entry_points={
        "console_scripts": [
            "ipyflow = ipyflow:main"
        ],
    },
    license="BSD-3-Clause",
    zip_safe=False,
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Natural Language :: English",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)

# python setup.py sdist bdist_wheel --universal
# twine upload dist/*
