nbsafety
========

[![](https://github.com/runtime-jupyter-safety/nbsafety/workflows/master/badge.svg)](https://github.com/runtime-jupyter-safety/nbsafety/actions)
[![License: BSD3](https://img.shields.io/badge/License-BSD3-maroon.svg)](https://opensource.org/licenses/BSD-3-Clause)
![](https://img.shields.io/pypi/v/nbsafety.svg)
![](https://img.shields.io/pypi/pyversions/nbsafety.svg)

Install
-------
To install, grab the package and install the Jupyter KernelSpec as follows:
```
pip install nbsafety
```

Running
-------

To run an `nbsafety` kernel, select "Python 3 (nbsafety)" from the list
of notebook types in Jupyter's "New" dropdown dialogue:

![](img/nbsafety.png)

Future
------

Currently `nbsafety` is supported for Jupyter notebooks (not JupyterLab).
JupyterLab support is targeted for a future release.

Uninstall
---------
```
jupyter kernelspec uninstall python3-nbsafety
```

License
-------
Code in this project licensed under the [BSD-3-Clause License](https://opensource.org/licenses/BSD-3-Clause).
