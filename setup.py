#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
from setuptools import setup, find_packages


def read_file(fname):
    with open(fname, 'r') as f:
        return f.read()


history = read_file('HISTORY.rst')
requirements = read_file('requirements.txt').strip().split()
pkg_name = 'nbsafety'
exec(read_file(os.path.join(pkg_name, 'version.py')))
setup(
    name=pkg_name,
    version=__version__,  # noqa
    author='Stephen Macke, Ray Gong',
    author_email='stephen.macke@gmail.com',
    description='Fearless interactive computing for Jupyter notebooks.',
    long_description=read_file('README.md'),
    url='https://github.com/runtime-jupyter-safety/nbsafety',  # maybe rename to nbsafety
    packages=find_packages(exclude=['docs', 'scratchspace', 'notebooks']),
    include_package_data=True,
    install_requires=requirements,
    license='MIT',
    zip_safe=False,
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Natural Language :: English',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
    ],
)

# python setup.py sdist bdist_wheel --universal
# twine upload dist/*
