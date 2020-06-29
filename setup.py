#!/usr/bin/env python
# -*- coding: utf-8 -*-

import contextlib
import json
import os
import sys
from setuptools import setup, find_packages
from tempfile import TemporaryDirectory

import versioneer

pkg_name = 'nbsafety'

DISPLAY_NAME = f"Python 3 ({pkg_name})"
KERNEL_JSON = {
    "argv": [
        sys.executable, "-m", "nbsafety.kernel", "-f", "{connection_file}",
    ],
    "display_name": DISPLAY_NAME,
    "language": "python",
    "codemirror_mode": "shell",
}


def read_file(fname):
    with open(fname, 'r', encoding='utf8') as f:
        return f.read()


history = read_file('HISTORY.rst')
requirements = read_file('requirements.txt').strip().split()

if sys.argv[-1] == 'install':
    should_dump_kernel_json = True
    tempdir_or_nullcontext = TemporaryDirectory
else:
    should_dump_kernel_json = False
    tempdir_or_nullcontext = contextlib.nullcontext
with tempdir_or_nullcontext() as td:
    extra_data_files = []
    if should_dump_kernel_json:
        os.chmod(td, 0o755)  # Starts off as 700, not user readable
        kernel_json_file = os.path.join(td, 'kernel.json')
        with open(kernel_json_file, 'w') as f:
            json.dump(KERNEL_JSON, f, sort_keys=True)
        extra_data_files.append(("share/jupyter/kernels/nbsafety", [os.path.relpath(kernel_json_file, os.curdir)]))
    setup(
        name=pkg_name,
        version=versioneer.get_version(),
        cmdclass=versioneer.get_cmdclass(),
        author='Stephen Macke',
        author_email='stephen.macke@gmail.com',
        description='Fearless interactivity for Jupyter notebooks.',
        long_description=read_file('README.md'),
        long_description_content_type='text/markdown',
        url='https://github.com/nbsafety-project/nbsafety',
        packages=find_packages(exclude=[
            'binder',
            'docs',
            'scratchspace',
            'notebooks',
            'img',
            'test',
            'scripts',
            'markdown',
            'versioneer.py',
            'frontend',
            'blueprint.json',
        ]),
        include_package_data=True,
        data_files=[
            # like `jupyter nbextension install --sys-prefix`
            ("share/jupyter/nbextensions/nbsafety", [
                "nbsafety/resources/nbextension/index.js",
            ]),
            ("share/jupyter/nbextensions/nbsafety", [
                "nbsafety/resources/nbextension/index.js.map",
            ]),
            # like `jupyter nbextension enable --sys-prefix`
            ("etc/jupyter/nbconfig/notebook.d", [
                "nbsafety/resources/nbextension/nbsafety.json",
            ]),
            # like `python -m nbsafety.install --sys-prefix`
            ("share/jupyter/kernels/nbsafety", [
                "nbsafety/resources/kernel/logo-32x32.png",
            ]),
            ("share/jupyter/kernels/nbsafety", [
                "nbsafety/resources/kernel/logo-64x64.png",
            ]),
        ] + extra_data_files,
        install_requires=requirements,
        license='BSD-3-Clause',
        zip_safe=False,
        classifiers=[
            'Development Status :: 3 - Alpha',
            'Intended Audience :: Developers',
            'License :: OSI Approved :: BSD License',
            'Natural Language :: English',
            'Programming Language :: Python :: 3.6',
            'Programming Language :: Python :: 3.7',
        ],
    )

# python setup.py sdist bdist_wheel --universal
# twine upload dist/*
