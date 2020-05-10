nbsafety
========

[![](https://github.com/runtime-jupyter-safety/nbsafety/workflows/master/badge.svg)](https://github.com/runtime-jupyter-safety/nbsafety/actions)
[![Checked with mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)
[![License: BSD3](https://img.shields.io/badge/License-BSD3-maroon.svg)](https://opensource.org/licenses/BSD-3-Clause)
[![](https://img.shields.io/pypi/v/nbsafety.svg)](https://pypi.org/project/nbsafety)
![](https://img.shields.io/pypi/pyversions/nbsafety.svg)

About
-----
`nbsafety` adds a layer of protection to computational notebooks by solving the
*stale dependency problem*, a problem which exists due to the fact that
notebooks segment execution into "cells" with implicit dependencies amongst
themselves. Here's an example in action:

![](https://raw.githubusercontent.com/runtime-jupyter-safety/nbsafety/master/img/nbsafety-demo.gif)

`nbsafety` accomplishes its magic using a combination of a runtime tracer (to
build the implicit dependency graph) and a static checker (to provide warnings
before running a cell), both of which are deeply aware of Python's data model.
In particular, `nbsafety` requires ***minimal to no changes*** in user
behavior, opting to get out of the way unless absolutely necessary and letting
you use notebooks the way you prefer.

Install
-------
To install, grab the package and install the Jupyter KernelSpec as follows:
```bash
pip install nbsafety
```

Running
-------

Because `nbsafety` is implemented as a custom Jupyter kernel, it works for
both Jupyter notebooks and JupyterLab.
To run an `nbsafety` kernel, select "Python 3 (nbsafety)" from the list
of notebook types in Jupyter's "New" dropdown dialogue:

![](https://raw.githubusercontent.com/runtime-jupyter-safety/nbsafety/master/img/nbsafety-notebook.png)

For JupyterLab, similarly select "Python 3 (nbsafety)" from the list
of available kernels in the Launcher tab:

![](https://raw.githubusercontent.com/runtime-jupyter-safety/nbsafety/master/img/nbsafety-lab.png)

Uninstall
---------
In addition to `pip uninstall nbsafety`, it is also necessary
to deregister the kernel from Jupyter for a full uninstall:
```bash
jupyter kernelspec uninstall nbsafety
```

License
-------
Code in this project licensed under the [BSD-3-Clause License](https://opensource.org/licenses/BSD-3-Clause).
