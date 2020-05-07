nbsafety
========

[![](https://github.com/runtime-jupyter-safety/nbsafety/workflows/master/badge.svg)](https://github.com/runtime-jupyter-safety/nbsafety/actions)
[![License: BSD](https://img.shields.io/badge/License-BSD-maroon.svg)](https://opensource.org/licenses/BSD-3-Clause)
![](https://img.shields.io/pypi/pyversions/nbsafety.svg)

Install
-------
To install, grab the package and install the Jupyter KernelSpec as follows:
```
pip install nbsafety
python -m nbsafety.install
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
