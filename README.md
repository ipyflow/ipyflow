nbsafety
========

[![](https://github.com/runtime-jupyter-safety/nbsafety/workflows/master/badge.svg)](https://github.com/runtime-jupyter-safety/nbsafety/actions)
[![Checked with mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)
[![License: BSD3](https://img.shields.io/badge/License-BSD3-maroon.svg)](https://opensource.org/licenses/BSD-3-Clause)
![](https://img.shields.io/pypi/v/nbsafety.svg)
![](https://img.shields.io/pypi/pyversions/nbsafety.svg)

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
