# nbsafety

{{ load:markdown-blueprints/badges.md }}

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

{{ template:contributors }}
