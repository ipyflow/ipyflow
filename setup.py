#!/usr/bin/env python
# -*- coding: utf-8 -*-
from glob import glob
from setuptools import setup, find_packages

import versioneer

pkg_name = 'nbsafety'


def read_file(fname):
    with open(fname, 'r', encoding='utf8') as f:
        return f.read()


history = read_file('HISTORY.rst')
requirements = read_file('requirements.txt').strip().split()

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
            "nbsafety/resources/nbextension/index.js.map",
        ]),
        # like `jupyter nbextension enable --sys-prefix`
        ("etc/jupyter/nbconfig/notebook.d", [
            "nbsafety/resources/nbextension/nbsafety.json",
        ]),
        ("share/jupyter/labextensions/jupyterlab-nbsafety",
            glob("nbsafety/resources/labextension/package.json")
        ),
        ("share/jupyter/labextensions/jupyterlab-nbsafety/static",
            glob("nbsafety/resources/labextension/static/*")
        ),
        # like `python -m nbsafety.install --sys-prefix`
        ("share/jupyter/kernels/nbsafety", [
            "nbsafety/resources/kernel/kernel.json",
            "nbsafety/resources/kernel/logo-32x32.png",
            "nbsafety/resources/kernel/logo-64x64.png",
        ]),
    ],
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
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
)

# python setup.py sdist bdist_wheel --universal
# twine upload dist/*
