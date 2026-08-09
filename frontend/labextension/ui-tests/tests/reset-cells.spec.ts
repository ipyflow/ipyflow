import { expect, test } from '@jupyterlab/galata';

import {
  attachNotebookDumpOnFailure,
  cellOutputText,
  editCellAndSync,
  enableReactiveMode,
  execCount,
  openIpyflowNotebook,
  settleAutosave,
  waitForEdge
} from './helpers';

/**
 * Regression tests for https://github.com/ipyflow/ipyflow/issues/173.
 *
 * When a cell is edited, the reactive closure has to pull in the *upstream*
 * cells that reset the state it consumed, not just its downstream dependents.
 * Otherwise the edited cell replays against state the previous run already
 * mutated, so its output compounds (or it raises). Both cases below did exactly
 * that before the fix, and both are only reachable end-to-end: the kernel
 * reports the stale parents, and the extension decides which of them the pulled
 * closure actually reaches (src/graph/closure.ts).
 *
 * Each notebook is run cell-by-cell through the *patched* run command first so
 * ipyflow builds its dependency graph (`page.notebook.run()` does not populate
 * it), reactive mode is switched on afterwards so those bootstrap runs do not
 * kick off cascades of their own, and the edited cell is then re-run with a raw
 * keypress -- galata's runCell cannot await ipyflow's reactive execution.
 */
test.describe('issue 173: reset cells are pulled into the rerun set', () => {
  attachNotebookDumpOnFailure(test);

  test('re-runs the cell that built a container mutated in place', async ({
    page
  }) => {
    test.setTimeout(180_000);
    await openIpyflowNotebook(page, [
      'import copy',
      'll = [[1],[2],[3]]',
      'c_l = copy.deepcopy(ll)',
      'c_l[0].append(5)',
      'print(c_l)'
    ]);
    for (let i = 0; i < 5; i++) {
      await page.notebook.runCell(i, true);
    }
    await waitForEdge(page, 2, 3);
    await enableReactiveMode(page);
    expect(await cellOutputText(page, 4)).toContain('[[1, 5], [2], [3]]');

    // The mutation lands on a namespace member, which does not bump the
    // container symbol's own shallow timestamp; the deepcopy cell still has to
    // be recognized as the stale parent that resets `c_l`.
    const deepcopyRuns = await execCount(page, 2);
    await editCellAndSync(page, 3, 'c_l[0].append(10)');
    await page.notebook.selectCells(3);
    await page.keyboard.press('Control+Enter');

    // Without re-running the deepcopy the appends compound to [[1, 5, 10], ...].
    await expect
      .poll(() => cellOutputText(page, 4), {
        timeout: 60_000,
        message: 'cell 4 never showed the un-compounded value'
      })
      .toContain('[[1, 10], [2], [3]]');
    expect(await execCount(page, 2)).toBeGreaterThan(deepcopyRuns as number);

    await settleAutosave(page);
  });

  test('re-runs a reset cell whose edge was pruned from the graph', async ({
    page
  }) => {
    test.setTimeout(180_000);
    // `a, b = b, a` has cells 0 and 1 writing their symbols at the same
    // timestamp, so only the later of the two survives in the parent map the
    // kernel reports. Cell 0 is still what restores `b`, so it has to be reached
    // through the stale-parent links rather than through that map.
    await openIpyflowNotebook(page, [
      'a = [1,2,3]',
      'b = [4,5,6]',
      'a, b = b, a',
      'b.append(4)',
      'print("a:", a, "b:", b)'
    ]);
    for (let i = 0; i < 5; i++) {
      await page.notebook.runCell(i, true);
    }
    await waitForEdge(page, 2, 3);
    await enableReactiveMode(page);
    expect(await cellOutputText(page, 4)).toContain(
      'a: [4, 5, 6] b: [1, 2, 3, 4]'
    );

    const firstAssignRuns = await execCount(page, 0);
    await editCellAndSync(page, 3, 'b.append(6)');
    await page.notebook.selectCells(3);
    await page.keyboard.press('Control+Enter');

    // Without re-running cells 0-2, `b` is still the post-swap list the previous
    // run appended to, and the output reads `b: [4, 5, 6, 6]`.
    await expect
      .poll(() => cellOutputText(page, 4), {
        timeout: 60_000,
        message: 'cell 4 never showed the value from a reset `b`'
      })
      .toContain('a: [4, 5, 6] b: [1, 2, 3, 6]');
    expect(await execCount(page, 0)).toBeGreaterThan(firstAssignRuns as number);

    await settleAutosave(page);
  });
});
