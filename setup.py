#!/usr/bin/env python
# -*- coding: utf-8 -*-

import platform
import os
from setuptools import setup, find_packages
from setuptools.command.develop import develop
from setuptools.command.install import install
from subprocess import check_call

pkg_name = 'nbsafety'


def make_post_install_hook(install_or_develop):
    class PostInstallHook(install_or_develop):
        """Post-installation for installation mode."""
        def run(self):
            install_or_develop.run(self)
            python_command_suffix = ''
            if platform.system().lower().startswith('win'):
                python_command_suffix = '.exe'
            check_call(f'python{python_command_suffix} -m {pkg_name}.install'.split())
    return PostInstallHook


def read_file(fname):
    with open(fname, 'r') as f:
        return f.read()


history = read_file('HISTORY.rst')
requirements = read_file('requirements.txt').strip().split()
exec(read_file(os.path.join(pkg_name, 'version.py')))
setup(
    name=pkg_name,
    version=__version__,  # noqa
    author='Stephen Macke, Ray Gong',
    author_email='stephen.macke@gmail.com',
    description='Fearless interactivity for Jupyter notebooks.',
    long_description=read_file('README.md'),
    url='https://github.com/runtime-jupyter-safety/nbsafety',
    packages=find_packages(exclude=['docs', 'scratchspace', 'notebooks', 'img', 'test']),
    include_package_data=True,
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
    cmdclass={
        'develop': make_post_install_hook(develop),
        'install': make_post_install_hook(install),
    },
)

# python setup.py sdist bdist_wheel --universal
# twine upload dist/*
