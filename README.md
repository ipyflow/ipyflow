<!-- ⚠️ This README has been generated from the file(s) "markdown-blueprints/README.md" ⚠️-->
[![-----------------------------------------------------](https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/colored.png)](#nbsafety)

# ➤ nbsafety


[![](https://github.com/nbsafety-project/nbsafety/workflows/nbsafety/badge.svg)](https://github.com/nbsafety-project/nbsafety/actions)
[![Checked with mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)
[![License: BSD3](https://img.shields.io/badge/License-BSD3-maroon.svg)](https://opensource.org/licenses/BSD-3-Clause)
[![](https://img.shields.io/pypi/v/nbsafety.svg)](https://pypi.org/project/nbsafety)
![](https://img.shields.io/pypi/pyversions/nbsafety.svg)
[![Binder](https://img.shields.io/badge/launch-binder-E66581.svg?logo=data:image/png;base64,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
)](https://mybinder.org/v2/gh/nbsafety-project/nbsafety/master?urlpath=lab/tree/notebooks/demo.ipynb)


About
-----
`nbsafety` adds a layer of protection to computational notebooks by solving the
*stale dependency problem* when executing cells out-of-order. Here's an
example in action:

Step 0: modify cell 1     | Step 1: rerun cell 1     
:------------------------:|:------------------------:
![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/example-0.png)  |![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/example-1.png)  

Step 2: rerun cell 2     | Step 3: rerun cell 3
:------------------------:|:------------------------:
![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/example-2.png)  |![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/example-3.png)

When the first cell is rerun, the second cell now contains a reference to an
updated `f` and is suggested for re-execution with a turquoise highlight. The
third cell contains a reference to a stale `y` -- `y` is stale due to its
dependency on an old value of `f`. As such, the third cell is marked as unsafe
for re-execution with a red highlight.  Once the second cell is rerun, it is
now suggested to re-execute the third cell in order to refresh its stale
output.


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

Interface
---------
The kernel ships with an extension that highlights cells with live references
to stale symbols using red UI elements. It furthermore uses turquoise highlights
for cells with live references to updated symbols, as well as for cells that
resolve staleness.

Running
-------

To run an `nbsafety` kernel in Jupyter, select "Python 3 (nbsafety)" from the
list of notebook types in Jupyter's "New" dropdown dialogue. For JupyterLab,
similarly select "Python 3 (nbsafety)" from the list of available kernels in
the Launcher tab.

Jupyter Notebook Entrypoint:     |  Jupyter Lab Entrypoint:
:-------------------------------:|:-------------------------:
![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/nbsafety-notebook.png) | ![](https://raw.githubusercontent.com/nbsafety-project/nbsafety/master/img/nbsafety-lab.png)

Uninstall
---------
```bash
pip uninstall nbsafety
```

License
-------
Code in this project licensed under the [BSD-3-Clause License](https://opensource.org/licenses/BSD-3-Clause).


[![-----------------------------------------------------](https://raw.githubusercontent.com/andreasbm/readme/master/assets/lines/colored.png)](#contributors)

## ➤ Contributors
	

| [<img alt="Stephen Macke" src="https://avatars1.githubusercontent.com/u/325653?s=460&v=4" width="100">](https://github.com/smacke) | [<img alt="Ray Gong" src="https://avatars1.githubusercontent.com/u/46979212?s=460&v=4" width="100">](https://github.com/ruiduoray) | [<img alt="Shreya Shankar" src="https://avatars.githubusercontent.com/u/6224969?s=460&v=4" width="100">](https://github.com/shreyashankar) |
|:--------------------------------------------------:|:--------------------------------------------------:|:--------------------------------------------------:|
| [Stephen Macke](https://github.com/smacke)       | [Ray Gong](https://github.com/ruiduoray)         | [Shreya Shankar](https://github.com/shreyashankar) |

