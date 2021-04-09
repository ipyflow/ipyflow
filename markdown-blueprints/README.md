# nbsafety

{{ load:markdown-blueprints/badges.md }}

[![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/jupytercon.png)](https://cfp.jupytercon.com/2020/schedule/presentation/274/tuesday-poster-session/)

About
-----
`nbsafety` adds a layer of protection to computational notebooks by solving the
*stale dependency problem*, a problem which exists due to the fact that
notebooks segment execution into "cells" with implicit dependencies amongst
themselves. Here's an example in action:

Step 0: modify cell 1     | Step 1: rerun cell 1     
:------------------------:|:------------------------:
![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/example-0.png)  |![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/example-1.png)  

Step 2: rerun cell 2     | Step 3: rerun cell 3
:------------------------:|:------------------------:
![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/example-2.png)  |![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/example-3.png)

When the first cell is rerun, the second cell now contains a reference to an updated `f` and
is suggested for re-execution with a turquoise highlight. The third cell contains a reference
to a stale `y` -- `y` is stale due to its dependency on an old value of `f`. As such, the third
cell is marked as unsafe for re-execution with a red highlight.
Once the second cell is rerun, it is now suggested to re-execute the third cell in order to
refresh its stale output.


`nbsafety` accomplishes its magic using a combination of a runtime tracer (to
build the implicit dependency graph) and a static checker (to provide warnings
before running a cell), both of which are deeply aware of Python's data model.
In particular, `nbsafety` requires ***minimal to no changes*** in user
behavior, opting to get out of the way unless absolutely necessary and letting
you use notebooks the way you prefer.

Install
-------
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
