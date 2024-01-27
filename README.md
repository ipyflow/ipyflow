<h1> <img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/ipyflow-logo.png" width="25"> IPyflow </h1>

[![](https://github.com/ipyflow/ipyflow/workflows/ipyflow/badge.svg)](https://github.com/ipyflow/ipyflow/actions)
[![Checked with mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![License: BSD3](https://img.shields.io/badge/License-BSD3-maroon.svg)](https://opensource.org/licenses/BSD-3-Clause)
[![](https://img.shields.io/pypi/v/ipyflow.svg)](https://pypi.org/project/ipyflow)
![](https://img.shields.io/pypi/pyversions/ipyflow.svg)

TL;DR
-----
Precise reactive Python notebooks for Jupyter[Lab]:

1. `pip install ipyflow`
2. Pick `Python 3 (ipyflow)` from the launcher or kernel selector.
3. For each cell execution, the (minimal) set of out-of-sync upstream and
   downstream cells also re-execute, so that executed cells appear as they
   would when running the notebook from top-to-bottom.

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/ipyflow-tldr.gif" />
</p>

About
-----
IPyflow is a next-generation Python kernel for JupyterLab and Notebook 7 that
tracks dataflow relationships between symbols and cells during a given
interactive session, thereby making it easier to reason about notebook state.
Here is a
[video](https://www.youtube.com/watch?v=mZZnDlyKk7g&t=8s)
of the JupyterCon talk introducing it (and corresponding
[slides](https://docs.google.com/presentation/d/1D9MSiIkwv7gjRr7jfNYZXki9TfkoUr4Yr-a06i0w_QU)).

If you'd like to skip the elevator pitch and skip straight to installation /
activation instructions jump to [quick start](#quick-start) below; otherwise,
keep reading to learn about IPyflow's philosophy and feature set.

Goals
-----
IPyflow provides bolt-on reactivity to Jupyter's default Python kernel, ipykernel.
It was was designed with the following goals in mind:
- **Full backwards-compatibility with ipykernel:** IPyflow aims to be a
  *drop-in replacement* for ipykernel, providing a strict superset of its
  features.
- **Precise dependency inference:** IPyflow understands dependencies between
  cells beyond just simple variables. For example, IPyflow understands when
  cell `B` depends on cell `A` because of a subscript reference `x[0]`, and is
  smart enough not to reactively execute cell `B` when some other part of `x`,
  e.g. `x[1]`, changes. As a result, it limits unnecessary re-execution to a
  bare minimum.
- **Fearless execution:** IPyflow attempts to enforce the following invariant:
  whenever you execute a cell, the resulting output appears as it would if you
  had performed a "restart + run all" operation. The implication is that you
  can execute basically any cell in the notebook and trust that It Just
  Works<sup>TM</sup>.


Quick Start
-----------
To install, run:
```bash
pip install ipyflow
```

To run an IPyflow kernel, select "Python 3 (ipyflow)" from the list of
available kernels in the Launcher tab. Similarly, you can switch to / from
IPyflow from an existing notebook by navigating to the "Change kernel" file
menu item:

Entrypoint                       |  Kernel Switcher
:-------------------------------:|:-------------------------------:
![](https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/ipyflow-launcher.png) | ![](https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/change-kernel-lab.png)


Features
--------


### Reactive execution model

IPyflow ships with extensions that bring reactivity to JupyterLab and Notebook
7 by default, similar to execution behavior offered in other notebooks such as
[Observable](https://observablehq.com/),
[Pluto.jl](https://github.com/fonsp/Pluto.jl), and
[Marimo](https://github.com/marimo-team/marimo).

IPyflow's reactivity behaves a little bit differently from the above, however,
as it was designed to meet the needs of Jupyter users in particular. When you
execute cell `C` with IPyflow, `C`'s output, the output of the cells `C`
depends on, and the output of the cells that depend on `C` all appear as they
would if the notebook were executed from top to bottom (e.g. via "restart and
run-all"). When you select some cell `C`, all the cells that would re-execute
when `C` is executed have an orange dot next to them, and cells that `C`
depends on but that are up-to-date and will not re-execute have purple dots:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/ipyflow-dots.gif" width="400" />
</p>

The cell dependency information is persisted to the notebook metadata, so that
you can jump to any cell after starting a fresh kernel session, run it, and be
confident that the output is what was intended by the notebook author:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/ipyflow-restart.gif" width="400" />
</p>


### Autosave and recovering prior executions

Because IPyflow peeks at runtime state in order to infer dependencies, it needs
to keep content of the notebook in sync with the kernel's memory state, even
across browser refreshes. As such, IPyflow enables autosave-on-change by
default, so that the kernel state, the notebook UI's in-memory state, and the
notebook file on disk are all in sync. If you accidentally overwrite a cell's
output that you wanted to keep, e.g. during a reactive execution, and autosave
overwrites the previous result on disk, fear not! IPyflow provides a library
utility called `reproduce_cell` to recover the input and output of previous
cell executions (within a given kernel session):

```python
from ipyflow import reproduce_cell
reproduce_cell(4, lookback=1)  # to reproduce the previous execution of cell 4
```

Example:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/reproduce-cell.gif" width="400" />
</p>


### Opting out of reactivity

If you'd like to temporarily opt out of reactive execution, you can use
ctrl+shift+enter (on Mac, cmd+shift+enter also works) to only execute the cell in question:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/alt-mode-execute.gif" />
</p>

You can also run the magic command `%flow mode normal` in opt out of the
default reactive execution mode (in which case, ctr+shift+enter /
cmd+shift+enter will switch from being nonreactive to reactive). To reenable
reactive execution as the default, you can run `%flow mode reactive`:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/reactivity-opt-out.gif" />
</p>

If you'd like to prevent the default reactive behavior for every new kernel
session, you can add this to your IPython profile (default location typically
at `~/.ipython/profile_default/ipython_config.py`):

```python
c = get_config()
c.ipyflow.exec_mode = "normal"  # defaults to "reactive"
```


### In-order and any-order semantics

IPyflow defaults to *in-order* semantics, meaning that, if cell `B` depends on
cell `A`, then `A` must appear before `B` in the spatial order of the notebook.
IPyflow doesn't prevent previous cells from referencing data created or updated
by later cells, but it omits these edges when performing reactive execution.

In-order semantics, though less flexible, have some desirable properties when
compared with any-order semantics, as they encourage cleaner and more
reproducible notebooks that can more easily be converted to Python scripts
later. Now that I may or may not have sold you on in-order semantics, you can
enable any-order semantics in IPyflow by running the magic command `%flow
direction any_order`, and reenable the default in-order semantics using `%flow
direction in_order`:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/ipyflow-direction.gif" />
</p>

You can also update your IPython profile if you'd like to make any-order
semantics the default behavior for new kernel sessions:

```python
c = get_config()
c.ipyflow.flow_direction = "any_order"  # defaults to "in_order"
```


### Execution suggestions and shortcut for resolving inconsistencies

Whenever a cell references updated data, the collapser next to it is given an
orange color (similar to the color for dirty cells), and cells that
(recursively) depend on it are given a purple collapser color. (An orange input
with a purple output just means that the output may be out-of-sync.) When using
reactive execution, you usually won't see these, since out-of-sync dependent
cells will be rerun automatically, though you may see them if using
ctrl+shift+enter to temporarily opt out of reactivity, or if you change which
data the cell updates (thereby overwriting previous edges between cells).

If you'd like to let IPyflow fix these up for you, you can press "Space" when
in command mode to automatically resolve all stale or dirty cells. This
operation may introduce more stale cells, in which case you can continue
pressing "Space" until all inconsistencies are resolved, if desired:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/resolve-inconsistencies.gif" width="450" />
</p>


### Memoization

Cells that reference Python functions and classes, primitives like integers,
floats, strings, as well as numpy arrays, pandas dataframes, and containers
(lists, dicts, sets, tuples, etc.) thereof can be memoized by IPyflow using the
special `%%memoize` pseudomagic. There's no need to specify the "inputs" to the
cell, as IPyflow will infer these automatically. Memoized cells cache their
results in-memory (though disk-backed caches are planned for the future), and
will retrieve these cached results (rather than re-running the cell) whenever
IPyflow detects inputs and cell content identical to that of a previous run:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/ipyflow-memoization.gif" />
</p>

By default, `%%memoize` skips all output except potential displayhook output
from the last expression in the cell (when applicable). To skip this too, pass
`--quiet`, and to include stdout, stderr, and other rich output, pass
`--verbose`:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/memoize-quiet-verbose.gif" />
</p>


### IPyWidgets integration

IPyflow's reactive execution engine has built-in support for `ipywidgets`,
allowing widget changes to be propagated across cell boundaries:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/ipywidgets-integration.gif" width="500" />
</p>

This functionality can be combined with the `%%memoize` magic to provide near
real-time rendering of interactive plots across cells:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/ipywidgets-memoization.gif" width="500" />
</p>

This functionality can be paired with other extensions like
[stickyland](https://github.com/xiaohk/stickyland) to build fully reactive
dashboards on top of JupyterLab + IPyflow.

Finally, IPyflow also integrates with [mercury](https://github.com/mljar/mercury) widgets as well:

<p align="center">
<img src="https://raw.githubusercontent.com/ipyflow/ipyflow/master/img/ipyflow-mercury.gif" width="500" />
</p>

## State API

IPyflow must understand the underlying execution state at a deep level in
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

You can also do this at the cell-level as well using the `slice()` method:
```python
from ipyflow import cells
print(cells(4).slice())

# Output:
"""
# Cell 2
x = 0

# Cell 3
y = x + 1

# Cell 4
print(code(y))
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
IPyflow, use the `lift` function (at your own risk, of course):

```python
from ipyflow import lift

y_sym = lift(y)
y_sym.timestamp
# Timestamp(cell_num=3, stmt_num=0)
```

Colab, VSCode, and other Interfaces
-----------------------------------
Reactivity and other frontend features are not yet working in interfaces like
Colab or VSCode, but you can still use IPyflow's dataflow API on these surfaces
by initializing your notebook session with the following code:
```
%pip install ipyflow
%load_ext ipyflow
```

Citing
------
IPyflow started its life under the name nbsafety, which provided the initial
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

For anything not covered in the above papers, you can cite the IPyflow repo:
```bibtex
@misc{ipyflow,
  title = {{IPyflow: A Next-Generation, Dataflow-Aware IPython Kernel}},
  howpublished = {\url{https://github.com/ipyflow/ipyflow}},
  year = {2022},
}
```

Acknowledgements
----------------
IPyflow would not have been possible without the amazing academic collaborators
listed on the above papers. Its reactive execution features are inspired by
those of other excellent tools like [Hex](https://hex.tech/) notebooks,
[Pluto.jl](https://github.com/fonsp/Pluto.jl), and
[Observable](https://observablehq.com/). IPyflow also enjoys cross-pollination
of ideas with other reactive Python notebooks like
[Marimo](https://github.com/marimo-team/marimo),
[Jolin.io](https://cloud.jolin.io/), and
[Datalore](https://blog.jetbrains.com/datalore/2021/10/11/revamped-reactive-mode-and-how-it-makes-your-notebooks-reproducible/)
--- definitely check them out as well if you like IPyflow.

Work on IPyflow has benefited from the support of folks from a number of
companies -- both in the form of direct financial contributions
([Databricks](https://www.databricks.com/), [Hex](https://hex.tech/)) as well
as indirect moral support and encouragement ([Ponder](https://ponder.io/),
[Meta](https://www.meta.com/)). And of course, IPyflow rests on the foundations
built by the incredible Jupyter community.

License
-------
Code in this project licensed under the [BSD-3-Clause License](https://opensource.org/licenses/BSD-3-Clause).
