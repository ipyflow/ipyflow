import { expect, test } from '@jupyterlab/galata';

/**
 * Shared helpers for the ipyflow Galata / Playwright end-to-end tests.
 *
 * The extension exposes the active session's store on `window.ipyflow`
 * (state/registry.ts → setDebugStore), so the tests observe ipyflow's frontend
 * state (comm status, dependency graph, ready/waiting sets, settings) by reading
 * that handle via `page.evaluate`. JupyterLab itself is exposed on
 * `window.jupyterapp` (Galata), which we use to drive the kernel directly (e.g.
 * a restart) without going through a confirmation dialog.
 */

/** Poll until the ipyflow comm has established (window.ipyflow is the store). */
export async function waitForComm(page: any): Promise<void> {
  await expect
    .poll(
      () =>
        page.evaluate(
          () => (window as any).ipyflow?.isIpyflowCommConnected ?? false
        ),
      { timeout: 60_000, message: 'ipyflow comm never connected' }
    )
    .toBe(true);
}

/**
 * Create a notebook on the ipyflow kernel, populate `cells`, await the comm.
 * Returns the notebook's name (e.g. for `page.notebook.activate(name)` when
 * juggling several notebooks).
 */
export async function openIpyflowNotebook(
  page: any,
  cells: string[]
): Promise<string> {
  const name = await page.notebook.createNew(undefined, { kernel: 'ipyflow' });
  // Wait for the comm to establish on the fresh (empty) notebook BEFORE adding
  // cells. The establish handler flips the notebook into windowed-scrollbar
  // mode and re-renders; letting that happen concurrently with galata's cell
  // construction races and can hang addCell (the cell DOM shifts underneath it).
  await waitForComm(page);
  await buildCells(page, cells);
  return name;
}

/**
 * Replace the foreground notebook's cells with exactly `sources` code cells, via
 * the shared model. This is deterministic and immune to the editor/race issues
 * of Galata's setCell/addCell -- the latter retypes into CodeMirror (which can
 * append in ipyflow's windowed notebook, see setCellSource) and fires an
 * *unawaited* insert-cell-below that leaves a stray trailing empty cell. Reads
 * the model off the current panel, so it works on any kernel (not just ipyflow).
 */
export async function buildCells(page: any, sources: string[]): Promise<void> {
  await page.evaluate((srcs: string[]) => {
    const sm = (window as any).jupyterapp.shell.currentWidget.content.model
      .sharedModel;
    sm.transact(() => {
      if (sm.cells.length > 1) {
        sm.deleteCellRange(1, sm.cells.length);
      }
      sm.cells[0].setSource(srcs[0] ?? '');
      for (let i = 1; i < srcs.length; i++) {
        sm.insertCell(i, { cell_type: 'code', source: srcs[i] });
      }
    });
  }, sources);
}

/** The union of the store's ready + waiting cell-id sets. */
export const readyAndWaitingCells = (page: any): Promise<string[]> =>
  page.evaluate(() => {
    const s = (window as any).ipyflow;
    return s ? [...(s.readyCells ?? []), ...(s.waitingCells ?? [])] : [];
  });

/** The model id of the cell at `index`, as ipyflow keys its graph by it. */
export const cellModelId = (
  page: any,
  index: number
): Promise<string | undefined> =>
  page.evaluate(
    (i: number) => (window as any).ipyflow?.notebook?.widgets?.[i]?.model?.id,
    index
  );

/** The execution count of the cell at `index` (null if never executed). */
export const execCount = (page: any, index: number): Promise<number | null> =>
  page.evaluate(
    (i: number) =>
      (window as any).ipyflow?.notebook?.widgets?.[i]?.model?.executionCount ??
      null,
    index
  );

/**
 * The text of a cell's outputs, read from the output *model* (not the DOM, which
 * may be virtualized away in ipyflow's windowed-scrollbar mode). Concatenates
 * stream text, `text/plain` mime data, and error tracebacks (so a raised
 * exception is observable). Useful across a kernel restart, where execution
 * counts reset and so cannot be compared, but a changed printed value (or a new
 * error) still proves a cell re-executed.
 */
export const cellOutputText = (page: any, index: number): Promise<string> =>
  page.evaluate((i: number) => {
    const outputs = (window as any).ipyflow?.notebook?.widgets?.[i]?.model
      ?.outputs;
    if (!outputs) {
      return '';
    }
    let text = '';
    for (let k = 0; k < outputs.length; k++) {
      const json = outputs.get(k)?.toJSON?.() ?? {};
      if (typeof json.text === 'string') {
        text += json.text;
      }
      const plain = json.data?.['text/plain'];
      if (typeof plain === 'string') {
        text += plain;
      }
      if (json.output_type === 'error') {
        text += `${json.ename}: ${json.evalue}\n`;
        if (Array.isArray(json.traceback)) {
          text += json.traceback.join('\n');
        }
      }
    }
    return text;
  }, index);

/** The input source text of the cell at `index` (read from its shared model). */
export const cellSource = (page: any, index: number): Promise<string | null> =>
  page.evaluate(
    (i: number) =>
      (window as any).ipyflow?.notebook?.widgets?.[
        i
      ]?.model?.sharedModel?.getSource() ?? null,
    index
  );

/**
 * Set the input source of the cell at `index` directly on its shared model.
 *
 * Prefer this over Galata's `page.notebook.setCell`, which retypes the source
 * into the CodeMirror editor (select-all + type). After a kernel swap, ipyflow's
 * windowed notebook can leave the editor without a focused selection, so the
 * select-all is dropped and the new text is *inserted* rather than replacing the
 * old -- e.g. `setCell('x = 42')` over `x = 1` yields `x = 42x = 1`, a syntax
 * error that silently breaks the cell. Writing the model is immune to that and
 * still fires the content-changed notifications ipyflow listens for.
 */
export async function setCellSource(
  page: any,
  index: number,
  source: string
): Promise<void> {
  await page.evaluate(
    ({ i, src }: { i: number; src: string }) => {
      (window as any).ipyflow?.notebook?.widgets?.[
        i
      ]?.model?.sharedModel?.setSource(src);
    },
    { i: index, src: source }
  );
}

/**
 * Edit the cell at `index` and wait until the extension has shipped the new
 * source to the kernel.
 *
 * `setCellSource` returns as soon as the shared model is written, but the
 * `notify_content_changed` message that carries the edit is debounced (500ms,
 * see comm/notebookEvents.ts). Re-running the cell before that lands makes the
 * kernel compute the reactive closure from the *old* source, so any test that
 * edits and then re-runs has to wait for the send. `store.lastCellMetadataMap`
 * is written as part of that send, so it is the observable signal that it
 * happened.
 */
export async function editCellAndSync(
  page: any,
  index: number,
  source: string
): Promise<void> {
  await setCellSource(page, index, source);
  expect(await cellSource(page, index)).toBe(source);
  await expect
    .poll(
      () =>
        page.evaluate(
          ({ i, src }: { i: number; src: string }) => {
            const store = (window as any).ipyflow;
            const id = store?.notebook?.widgets?.[i]?.model?.id;
            return store?.lastCellMetadataMap?.[id]?.content === src;
          },
          { i: index, src: source }
        ),
      {
        timeout: 30_000,
        message: `edit to cell ${index} was never sent to the kernel`
      }
    )
    .toBe(true);
}

/**
 * Delete the cell at `index` via the shared model's `deleteCell`. Galata's
 * `deleteCells` (and the `notebook:delete-cell` command / `d d` shortcut) proved
 * unreliable here -- they didn't actually remove the cell -- whereas the model
 * edit is synchronous and fires the same `cells.changed` notification ipyflow
 * listens for.
 */
export async function deleteCell(page: any, index: number): Promise<void> {
  await page.evaluate((i: number) => {
    (window as any).ipyflow.notebook.model.sharedModel.deleteCell(i);
  }, index);
}

/** A Playwright Locator for the cell at `index` in the foreground notebook. */
export async function cellLocator(page: any, index: number) {
  const nb = await page.notebook.getNotebookInPanelLocator();
  return nb.locator('.jp-Cell').nth(index);
}

/**
 * The CSS class list of the cell node at `index`, read straight off the widget
 * (so membership checks are exact, avoiding substring traps like 'ipyflow-slice'
 * matching 'ipyflow-slice-execute' under a regex).
 */
export const cellClassList = (page: any, index: number): Promise<string[]> =>
  page.evaluate(
    (i: number) =>
      Array.from(
        (window as any).ipyflow?.notebook?.widgets?.[i]?.node?.classList ?? []
      ) as string[],
    index
  );

/** Poll until the cell at `index` carries (or, if `present` is false, drops) a class. */
export async function waitForCellClass(
  page: any,
  index: number,
  className: string,
  present = true
): Promise<void> {
  await expect
    .poll(async () => (await cellClassList(page, index)).includes(className), {
      timeout: 30_000,
      message: `cell ${index} never ${present ? 'gained' : 'lost'} class ${className}`
    })
    .toBe(present);
}

/**
 * The CSS class list of the minimap item that mirrors the cell at `index` in the
 * foreground notebook. ipyflow's windowed-scrollbar "minimap" is the
 * `li.jp-WindowedPanel-scrollbar-item` list, one item per cell in order; the
 * decorations code (ui/decorations.ts) tags those items with the same
 * `ipyflow-slice` / `ipyflow-slice-execute` classes as the cells to color-code
 * the slice. Reads off `window.ipyflow.notebook`, so it always reflects the
 * active tab's own notebook.
 */
export const minimapClassList = (
  page: any,
  index: number
): Promise<string[]> =>
  page.evaluate((i: number) => {
    const node = (window as any).ipyflow?.notebook?.node;
    if (!node) {
      return [];
    }
    const items = node.querySelectorAll(
      'div.jp-WindowedPanel-scrollbar > ol > li.jp-WindowedPanel-scrollbar-item'
    );
    return Array.from(items[i]?.classList ?? []) as string[];
  }, index);

/** Poll until the minimap item at `index` carries (or drops) a class. */
export async function waitForMinimapClass(
  page: any,
  index: number,
  className: string,
  present = true
): Promise<void> {
  await expect
    .poll(async () => (await minimapClassList(page, index)).includes(className), {
      timeout: 30_000,
      message: `minimap item ${index} never ${
        present ? 'gained' : 'lost'
      } class ${className}`
    })
    .toBe(present);
}

/**
 * Whether ipyflow's dependency graph records cell `parentIndex` as a parent of
 * cell `childIndex` (i.e. the edge parent → child is known to the frontend).
 */
export const cellChildrenIncludes = (
  page: any,
  parentIndex: number,
  childIndex: number
): Promise<boolean> =>
  page.evaluate(
    ({ p, c }: { p: number; c: number }) => {
      const s = (window as any).ipyflow;
      const pid = s?.notebook?.widgets?.[p]?.model?.id;
      const cid = s?.notebook?.widgets?.[c]?.model?.id;
      return (s?.cellChildren?.[pid] ?? []).includes(cid);
    },
    { p: parentIndex, c: childIndex }
  );

/**
 * Poll until the parent → child edge is present in the dependency graph (or,
 * when `present` is false, until it has been dropped).
 */
export async function waitForEdge(
  page: any,
  parentIndex: number,
  childIndex: number,
  present = true
): Promise<void> {
  await expect
    .poll(() => cellChildrenIncludes(page, parentIndex, childIndex), {
      timeout: 30_000,
      message: `dependency edge cell${parentIndex} -> cell${childIndex} never ${
        present ? 'formed' : 'dropped'
      }`
    })
    .toBe(present);
}

/** The store's current ipyflow exec mode ('lazy' | 'reactive' | undefined). */
export const execMode = (page: any): Promise<string | undefined> =>
  page.evaluate(() => (window as any).ipyflow?.settings?.exec_mode);

/** Poll until the store reports the given exec mode. */
export async function waitForExecMode(
  page: any,
  mode: 'lazy' | 'reactive'
): Promise<void> {
  await expect
    .poll(() => execMode(page), {
      timeout: 30_000,
      message: `exec mode never became ${mode}`
    })
    .toBe(mode);
}

/**
 * Select the cell at `index` (entering command mode) and press a key chord,
 * exercising one of ipyflow's notebook keybindings. `selectCells` leaves the
 * notebook focused in command mode, which is what the ipyflow bindings target.
 */
export async function pressOnCell(
  page: any,
  index: number,
  keys: string
): Promise<void> {
  await page.notebook.selectCells(index);
  await page.keyboard.press(keys);
}

/**
 * Restart with a fresh kernel and wait for ipyflow to re-establish its comm.
 *
 * We swap in a brand-new kernel of the same type via
 * `ISessionContext.changeKernel` rather than an in-place `KernelConnection`
 * restart. The two are equivalent for what these tests care about -- a kernel
 * with no execution history attached to the same notebook (so its persisted
 * `ipyflow` metadata still applies) -- but a fresh kernel is what the extension
 * actually wires reconnection to: `changeKernel` fires `session.kernelChanged`,
 * whose handler (comm/connect.ts) re-registers the `ipyflow-client` comm target
 * and reconnects, rebuilding the store. An in-place restart keeps the same
 * kernel id, fires no `kernelChanged`, and the comm never re-establishes.
 *
 * We null out `window.ipyflow` first so `waitForComm` cannot observe the stale
 * pre-restart store and return early; the reconnect path re-points the handle at
 * the fresh store (registry.setDebugStore) once it establishes.
 */
export async function restartKernel(page: any): Promise<void> {
  await page.evaluate(async () => {
    const sessionContext = (window as any).jupyterapp?.shell?.currentWidget
      ?.sessionContext;
    (window as any).ipyflow = null;
    await sessionContext.changeKernel({ name: 'ipyflow' });
  });
  await waitForComm(page);
}

/** Run a line of code on the foreground notebook's kernel (silently, off-cell). */
export async function runKernelCode(page: any, code: string): Promise<void> {
  await page.evaluate(async (src: string) => {
    const kernel = (window as any).ipyflow?.session?.session?.kernel;
    await kernel.requestExecute({ code: src }).done;
  }, code);
}

/** Set ipyflow's exec mode via `%flow mode <mode>` and wait for the store to reflect it. */
export async function setFlowMode(
  page: any,
  mode: 'lazy' | 'reactive'
): Promise<void> {
  await runKernelCode(page, `%flow mode ${mode}`);
  await waitForExecMode(page, mode);
}

/** Put ipyflow into reactive mode (convenience wrapper around {@link setFlowMode}). */
export async function enableReactiveMode(page: any): Promise<void> {
  await setFlowMode(page, 'reactive');
}

/**
 * A snapshot of every cell in the foreground notebook: source, execution count,
 * and output text. Read from the models, so it reflects the real notebook state
 * regardless of windowed-scrollbar virtualization. Handy for diagnosing failures
 * (e.g. a corrupted cell source from a stale edit) -- see
 * {@link attachNotebookDumpOnFailure}.
 */
export const dumpNotebook = (
  page: any
): Promise<
  Array<{
    index: number;
    id: string;
    source: string;
    executionCount: number | null;
    output: string;
  }>
> =>
  page.evaluate(() => {
    const widgets = (window as any).ipyflow?.notebook?.widgets ?? [];
    return widgets.map((w: any, index: number) => {
      const model = w?.model;
      const outputs = model?.outputs;
      let output = '';
      for (let k = 0; k < (outputs?.length ?? 0); k++) {
        const json = outputs.get(k)?.toJSON?.() ?? {};
        if (typeof json.text === 'string') {
          output += json.text;
        }
        const plain = json.data?.['text/plain'];
        if (typeof plain === 'string') {
          output += plain;
        }
        if (json.output_type === 'error') {
          output += `${json.ename}: ${json.evalue}\n`;
        }
      }
      return {
        index,
        id: model?.id,
        source: model?.sharedModel?.getSource?.() ?? '',
        executionCount: model?.executionCount ?? null,
        output
      };
    });
  });

/**
 * Register an afterEach hook that, when a test fails, attaches a JSON dump of the
 * notebook's cells (source / execution count / output) to the Playwright report.
 * Call this once inside a `test.describe` so a failed run shows the actual cell
 * contents -- which is exactly what was needed to spot a corrupted `x = 42x = 1`
 * source. Pass the `test` object from '@jupyterlab/galata'.
 */
export function attachNotebookDumpOnFailure(t: typeof test): void {
  t.afterEach(async ({ page }, testInfo) => {
    if (testInfo.status === testInfo.expectedStatus) {
      return;
    }
    try {
      const dump = await dumpNotebook(page);
      await testInfo.attach('notebook-cells', {
        body: JSON.stringify(dump, null, 2),
        contentType: 'application/json'
      });
    } catch {
      // Best-effort diagnostics only; never let this mask the real failure.
    }
  });
}

/**
 * After each schedule, ipyflow persists its dependency graph via a 200ms-
 * debounced notebook save. Galata deletes each test's temp directory on
 * teardown, so a save that fires mid-teardown 500s ("File Save Error"). Let the
 * debounced save flush while the notebook still exists before the test ends.
 */
export async function settleAutosave(page: any): Promise<void> {
  await page.waitForTimeout(1000);
}
