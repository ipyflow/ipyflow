# nbsafety

{{ load:markdown-blueprints/badges.md }}

About
-----
`nbsafety` adds a layer of protection to computational notebooks by solving the
*stale dependency problem*, a problem which exists due to the fact that
notebooks segment execution into "cells" with implicit dependencies amongst
themselves. Here's an example in action:

![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/nbsafety-demo.gif)

`nbsafety` accomplishes its magic using a combination of a runtime tracer (to
build the implicit dependency graph) and a static checker (to provide warnings
before running a cell), both of which are deeply aware of Python's data model.
In particular, `nbsafety` requires ***minimal to no changes*** in user
behavior, opting to get out of the way unless absolutely necessary and letting
you use notebooks the way you prefer.

:warning: Disclaimer :warning:
------------------------------
This project should be considered pre- or early alpha and may have bugs and
stability issues. It is not impossible that `nbsafety` could crash on good
code. We will remove this banner when the project is in a stabler state.
Fortunately, it not a matter of 'if', but 'when'. Until then, please file
issues for any bugs encountered!

Install
-------
To install, grab the package and install the Jupyter kernel spec.
```bash
pip install nbsafety
```

If using JupyterLab, we highly recommend installing the companion extension:
```bash
jupyter labextension install jupyterlab-nbsafety  # optional but highly recommended if using JupyterLab
```

Interface
---------
The JupyterLab extension and bundled Jupyter notebook extension both show cells
with unsafe executions (due to uses of variables with stale dependencies) as
being annotated with red UI elements, and recommends cells to run (in order to
refresh variables with stale dependencies) by displaying them with turquoise UI
elements.

Running
-------

Because `nbsafety` is implemented as a custom Jupyter kernel, it works for both
Jupyter notebooks and JupyterLab (if using JupyterLab, the additional
labextension is recommended).  To run an `nbsafety` kernel, select "Python 3
(nbsafety)" from the list of notebook types in Jupyter's "New" dropdown
dialogue.  For JupyterLab, similarly select "Python 3 (nbsafety)" from the list
of available kernels in the Launcher tab.

Jupyter Notebook Entrypoint:     |  Jupyter Lab Entrypoint:
:-------------------------------:|:-------------------------:
![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/nbsafety-notebook.png) | ![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/nbsafety-lab.png)

Troubleshooting Install
-----------------------
The kernel and nbextension should be installed automatically, but in case
the kernel is not available as an option or the UI elements are not showing
up, try running the following:
```bash
python -m nbsafety.install
jupyter nbextension install --py nbsafety --sys-prefix
jupyter nbextension enable --py nbsafety --sys-prefix
```

Uninstall
---------
In addition to `pip uninstall nbsafety`, it is also necessary
to deregister the kernel from Jupyter for a full uninstall
(as well as the extension from JupyterLab, if using JupyterLab):
```bash
jupyter kernelspec uninstall nbsafety
jupyter labextension uninstall jupyterlab-nbsafety
```

License
-------
Code in this project licensed under the [BSD-3-Clause License](https://opensource.org/licenses/BSD-3-Clause).

{{ template:contributors }}
