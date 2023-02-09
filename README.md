# IPyflow


[![](https://github.com/ipyflow/ipyflow/workflows/ipyflow/badge.svg)](https://github.com/ipyflow/ipyflow/actions)
[![Checked with mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![License: BSD3](https://img.shields.io/badge/License-BSD3-maroon.svg)](https://opensource.org/licenses/BSD-3-Clause)
[![](https://img.shields.io/pypi/v/ipyflow.svg)](https://pypi.org/project/ipyflow)
![](https://img.shields.io/pypi/pyversions/ipyflow.svg)
[![Binder](https://img.shields.io/badge/launch-binder-E66581.svg?logo=data:image/png;base64,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
)](https://mybinder.org/v2/gh/ipyflow/ipyflow/binder?urlpath=lab/tree/notebooks/demo.ipynb)

About
-----
`ipyflow` is a next-generation Python kernel for Jupyter and other notebook
interfaces that tracks dataflow relationships between symbols and cells during
a given interactive session. It aims to help notebook users reason about state
and avoid gotchas from out-of-order execution by providing features like
execution suggestions and reactivity. Keep reading to learn how. :)

Quick Start
-----------
To install, run:
```bash
pip install ipyflow
```

To run an `ipyflow` kernel in JupyterLab, select "Python 3 (ipyflow)" from the
list of available kernels in the Launcher tab. For classic Jupyter, similarly
select "Python 3 (ipyflow)" from the list of notebook types in the "New"
dropdown dialogue.

JupyterLab  Entrypoint:          |  Classic Jupyter Entrypoint:
:-------------------------------:|:---------------------------:
![](https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/ipyflow-lab.png) | ![](https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/ipyflow-notebook.png)

Similarly, you can switch to / from ipyflow from an existing notebook by
navigating to the "Change kernel" file menu item in either JupyterLab or
classic Jupyter:

JupyterLab Kernel Switcher:      |  Classic Jupyter Kernel Switcher:
:-------------------------------:|:--------------------------------:
![](https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/change-kernel-lab.png) | ![](https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/change-kernel-notebook.png)

Note: reactive execution features are not yet supported in classic Jupyter
notebooks, but we are working on it!

Features
--------
`ipyflow` ships with a JupyterLab extension that provides the following
user-facing features.

### Execution Suggestions

To keep the execution state consistent with the code in cells, rerun the
turquoise-highlighted cells, and avoid the red-highlighted cells:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/suggestions.gif" />
</p>

A turquoise-highlighted input with red-highlighted output just means that the
output may be out-of-sync.

### Reactivity

Do you trust me? Good. It's time to free yourself of the burden of manual
re-execution.  Use ctrl+shift+enter (on Mac, cmd+shift+enter also works) to
execute a cell and its (recursive) dependencies reactively:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/reactive-hotkey.gif" />
</p>

You can also run the magic command `%flow mode reactive` in any cell to enable
reactivity as the default execution mode:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/reactivity.gif" />
</p>

Disable by running `%flow mode normal`.

### Syntax Extensions

Prefixing a symbol with `$` in a load context will cause the referencing cell
to re-execute itself, whenever the aforementioned symbol is updated (regardless
of execution mode):

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/syntax-extensions-load.gif" />
</p>

You can also use the `$` syntax in store contexts, which triggers cells that
reference the corresponding symbol to re-execute, regardless of whether the
reference is similarly `$`-prefixed:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/syntax-extensions-store.gif" />
</p>

Finally, you can also prefix with `$$` to trigger a cascading reactive update
to all dependencies in the chain, recursively:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/syntax-extensions-cascading-store.gif" />
</p>

### Integration with ipywidgets

`ipyflow`'s reactive execution engine, as well as its APIs (see "State API" below)
are fully compatible with `ipywidgets`, allowing cells to respond to slider changes,
button clicks, and other events:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/ipywidgets-integration.gif" />
</p>

This functionality can be paired with other extensions like
[stickyland](https://github.com/xiaohk/stickyland) to build fully reactive
dashboards on top of JupyterLab + `ipyflow`.

## State API

`ipyflow` must understand the underlying execution state at a deep level in
order to provide its features. It exposes an API for interacting with some of
this state, including a `code` function for obtaining the code necessary to
reconstruct some symbol:

```python
# Cell 1
from ipyflow import code

# Cell 2
x = 0

# Cell 3
y = x + 1

# Cell 4
print(code(y))

# Output:
"""
# Cell 2
x = 0

# Cell 3
y = x + 1
"""
```

You can also see the cell (1-indexed) and statement (0-indexed) of when a
symbol was last updated with the `timestamp` function:

```python
from ipyflow import timestamp
timestamp(y)
# Timestamp(cell_num=3, stmt_num=0)
```

To see dependencies and dependents of a particular symbol, use the `deps` and
`users` fuctions, respectively:

```python
from ipyflow import deps, users

deps(y)
# [<x>]

users(x)
# [<y>]
```

If you want to elevate a symbol to the representation used internally by
`ipyflow`, use the `lift` function (at your own risk, of course):

```python
from ipyflow import lift

y_sym = lift(y)
y_sym.timestamp
# Timestamp(cell_num=3, stmt_num=0)
```

Finally, `ipyflow` also comes with some rudimentary support for watchpoints:

```python
# Cell 1
from ipyflow import watchpoints

def watchpoint(obj, position, symbol_name):
    if obj <= 42:
        return
    cell, line = position
    print(f"{symbol_name} = {obj} exceeds 42 at {cell=}, {line=}")

# Cell 2
y = 14
watchpoints(y).add(watchpoint)

# Cell 3
y += 10

# Cell 4
y += 20
# y = 44 exceeds 42 at cell=4, line=1
```

Citing
------
`ipyflow` started its life under the name `nbsafety`, which provided the initial
suggestions and slicing functionality.

For the [execution suggestions](http://www.vldb.org/pvldb/vol14/p1093-macke.pdf):
```bibtex
@article{macke2021fine,
  title={Fine-grained lineage for safer notebook interactions},
  author={Macke, Stephen and Gong, Hongpu and Lee, Doris Jung-Lin and Head, Andrew and Xin, Doris and Parameswaran, Aditya},
  journal={Proceedings of the VLDB Endowment},
  volume={14},
  number={6},
  pages={1093--1101},
  year={2021},
  publisher={VLDB Endowment}
}
```

For the [dynamic slicer](https://smacke.net/papers/nbslicer.pdf) (used for
reactivity and for the `code` function, for example):
```bibtex
@article{shankar2022bolt,
  title={Bolt-on, Compact, and Rapid Program Slicing for Notebooks},
  author={Shankar, Shreya and Macke, Stephen and Chasins, Andrew and Head, Andrew and Parameswaran, Aditya},
  journal={Proceedings of the VLDB Endowment},
  volume={15},
  number={13},
  pages={4038--4047},
  year={2022},
  publisher={VLDB Endowment}
}
```

We don't have a paper written yet for the syntax extensions that implement the
reactive algebra, but in the mean time, you can cite the `ipyflow` repo
directly for that and anything else not covered by the previous publications:
```bibtex
@misc{ipyflow,
  title = {{IPyflow: A Next-Generation, Dataflow-Aware IPython Kernel}},
  howpublished = {\url{https://github.com/ipyflow/ipyflow}},
  year = {2022},
}
```

License
-------
Code in this project licensed under the [BSD-3-Clause License](https://opensource.org/licenses/BSD-3-Clause).
