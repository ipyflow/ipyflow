# ipyflow extension UI / end-to-end tests

[Galata](https://github.com/jupyterlab/jupyterlab/tree/main/galata) + Playwright
tests that launch a real JupyterLab against the **ipyflow** kernel and exercise
the extension's UI behavior (comm establishment, dependency-aware cell
decoration, reactive re-execution).

## Test files

- `tests/helpers.ts` — shared plumbing (no tests). Reads ipyflow's per-session
  store off `window.ipyflow` and drives JupyterLab via `window.jupyterapp`:
  `openIpyflowNotebook` / `buildCells`, `waitForComm`, `waitForEdge` /
  `cellChildrenIncludes` (dependency graph), `execCount` / `cellOutputText` /
  `cellSource` / `cellClassList` / `waitForCellClass`, `setCellSource` /
  `deleteCell`, `setFlowMode` / `enableReactiveMode`, `restartKernel`, and
  `attachNotebookDumpOnFailure` (attaches a JSON dump of every cell's
  source/output/exec-count to the report when a test fails).
- `tests/ipyflow.spec.ts` — comm establishment, dependency-aware ready
  decoration, reactive re-execution.
- `tests/hotkeys.spec.ts` — the ipyflow keybindings: forward slice
  (`Accel+J` / `Accel+ArrowDown`), backward slice (`Accel+K` / `Accel+ArrowUp`),
  alt-mode execute (`Ctrl/Accel+Shift+Enter`), and run-ready-cells (`Space`).
  Each asserts the exact set of cells that (re)ran.
- `tests/decorations.spec.ts` — the decoration layer (`ui/decorations.ts`):
  forward/backward **slice highlighting** of the selection (`ipyflow-slice` /
  `ipyflow-slice-execute`), the `ready-cell` vs `waiting-cell` distinction for
  directly- vs transitively-stale dependents, and collapser-hover link
  highlighting.
- `tests/modes.spec.ts` — execution modes: an error mid-cascade aborts the
  downstream reactive re-execution; switching reactive → lazy stops dependents
  from auto-running.
- `tests/reset-cells.spec.ts` — regressions for
  [#173](https://github.com/ipyflow/ipyflow/issues/173): editing a cell whose
  upstream state was mutated in place must also re-run the **upstream** cell
  that resets it, both when the mutation lands on a namespace member
  (`c_l[0].append(...)` after a `deepcopy`) and when the reset cell's edge was
  pruned out of the reported parent map (the `a = [...]` above an
  `a, b = b, a` swap). Each asserts the un-compounded output *and* that the
  reset cell's execution count moved.
- `tests/notebook-ops.spec.ts` — deleting a cell keeps the surviving graph
  working; two notebooks keep independent graphs (store repoints on focus
  change); a cell run before the comm establishes still executes.
- `tests/commands.spec.ts` — the `alt-mode-execute` command (forward closure)
  and the run-and-advance bookkeeping (insert below the last cell vs advance).
- `tests/restart.spec.ts` — behavior across a kernel restart: the dependency
  graph persists (via notebook metadata) and reactive re-execution still works
  immediately after, and after idling.
- `tests/persistence.spec.ts` — the dependency graph survives closing and
  reopening the notebook (disk round-trip through the `.ipynb`).
- `tests/fallback.spec.ts` — on a non-ipyflow (`python3`) kernel the extension
  stays out of the way: cells run normally, no comm, no decorations.

### Gotchas worth knowing

- **Build / edit cells via the shared model, not Galata's editor APIs.**
  `openIpyflowNotebook` / `buildCells` and `setCellSource` write the shared model
  directly. Galata's `setCell`/`addCell` retype into the CodeMirror editor; in
  ipyflow's windowed-scrollbar notebook the editor can lose its selection, so the
  new text is _appended_ instead of replacing (e.g. `x = 1` → `x = 42x = 1`, a
  silent syntax error), and `addCell` fires an _unawaited_ insert-cell-below that
  leaves a stray trailing empty cell. The specs also assert `cellSource(...)`
  after editing to catch any recurrence.
- **`runCell(i)` defaults to Shift+Enter (run-and-advance)**, which inserts a new
  cell when run on the last cell. Pass `runCell(i, true)` (Control+Enter,
  in-place) when the exact cell count matters.
- **`getCellCount` / `widgets.length` are unreliable** in the windowed notebook
  (virtualized DOM); read `…model.sharedModel.cells.length` for the true count.
- **Reactive / closure runs happen outside Galata's run path** (ipyflow calls
  `CodeCell.execute` directly), so `page.notebook.runCell`'s `waitForRun` hangs
  on them. Trigger reactive runs with a raw `page.keyboard.press('Control+Enter')`
  and poll `execCount` / `cellOutputText` for the effect. `cellOutputText` also
  surfaces error tracebacks, so a raised exception is observable.
- **The dependency graph only populates after a _patched_ run** (`runCell`,
  Shift/Ctrl+Enter), not `page.notebook.run()`; `waitForEdge` waits for it.
- **`restartKernel` swaps in a fresh kernel via `changeKernel`** (firing
  `session.kernelChanged`, which the extension wires reconnection to). An
  in-place `KernelConnection.restart()` does not fire it and the comm never
  reconnects. A full `page.reload()` mid-test desyncs Galata's fixtures — close +
  reopen the notebook instead (see `persistence.spec.ts`).

## Prerequisites

The built extension and the ipyflow kernel must be installed in the active
environment. From the repo root:

```bash
make dev          # builds the labextension, symlinks it, installs the kernel
```

Verify:

```bash
jupyter labextension list   # jupyterlab-ipyflow ... enabled OK
jupyter kernelspec list     # ipyflow
```

## Running

```bash
cd frontend/labextension/ui-tests
npm install
npm run install-browser            # first time only (downloads chromium)
npm test                           # or: make uitest (from repo root)
```

Useful variants (run from this directory):

```bash
npx playwright test --headed   # watch the browser drive JupyterLab
npm run test:debug             # Playwright inspector
npm run test:report            # open the last HTML report (./playwright-report)
```

Note: the HTML report and other artifacts are written under
`frontend/labextension/ui-tests/` (not the repo root), so run
`npm run test:report` / `npx playwright show-report` from this directory.

### Recording video + trace

By default video/trace are kept only for failing tests. To capture a video and a
Playwright trace for **every** test (handy for watching the run or debugging a
green build), use the dedicated target from the repo root and then open the
report:

```bash
make uitest-record    # = make uitest, but with video + trace on for all tests
make uitest-report    # view the videos/traces in the HTML report
```

(Equivalently: `IPYFLOW_UITEST_RECORD=1 npm test` from this directory.)

Playwright launches its own JupyterLab via `jupyter_server_test_config.py` on a
dedicated port (**8899** by default, override with `IPYFLOW_UITEST_PORT`) so it
never collides with a JupyterLab you already have running on :8888. No manually
running server is required.

## CI

The `ui-tests` CI job is **opt-in** (it's slow and browser-based). It runs only
when the workflow is manually dispatched (Actions → CI → *Run workflow*) or on a
pull request carrying the `run-ui-tests` label. Ordinary pushes/PRs skip it.
