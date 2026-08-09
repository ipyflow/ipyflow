// Pure graph algorithms for computing reactive execution closures over the cell
// dependency graph. These functions take all of their inputs explicitly via an
// IClosureContext so they can be unit tested without any JupyterLab runtime --
// IpyflowSessionStore structurally satisfies IClosureContext and delegates here.

import { EdgeMap, ISettings, NestedEdgeMap } from '../types';

export interface IClosureContext {
  cellParents: EdgeMap;
  cellChildren: EdgeMap;
  executedCells: Set<string>;
  readyCells: Set<string>;
  waitingCells: Set<string>;
  dirtyCells: Set<string>;
  staleParents: EdgeMap;
  staleParentsByExecutedCellByChild: NestedEdgeMap;
  staleParentsByChildByExecutedCell: NestedEdgeMap;
  settings: ISettings;
}

export function computeRawTransitiveClosureHelper(
  ctx: IClosureContext,
  closure: Set<string>,
  cellId: string,
  edges: EdgeMap | undefined | null,
  pullReactiveUpdates = false,
  skipFirstCheck = false,
): void {
  if (!skipFirstCheck && closure.has(cellId)) {
    return;
  }
  if (!pullReactiveUpdates) {
    closure.add(cellId);
  }
  const relatives = edges?.[cellId];
  if (relatives === undefined) {
    return;
  }
  const prevClosureSize = closure.size;
  relatives.forEach((related) => {
    computeRawTransitiveClosureHelper(
      ctx,
      closure,
      related,
      edges,
      pullReactiveUpdates,
    );
  });
  if (
    pullReactiveUpdates &&
    (closure.size > prevClosureSize ||
      !ctx.executedCells.has(cellId) ||
      ctx.readyCells.has(cellId) ||
      ctx.waitingCells.has(cellId) ||
      ctx.dirtyCells.has(cellId))
  ) {
    closure.add(cellId);
  }
  if (!pullReactiveUpdates || !closure.has(cellId)) {
    return;
  }
  // A cell's stale parents are the ones that must re-run to restore the state it
  // consumed, so consider them even when the kernel pruned the corresponding edge
  // out of `cellParents` (which is deduped for display: when several parents write
  // a symbol at the same timestamp, only the last one survives). Without this, a
  // pruned reset cell -- e.g. the `a = [...]` above an `a, b = b, a` swap -- can
  // never be pulled into the closure. See ipyflow/ipyflow#173.
  const pullCandidates = new Set([
    ...relatives,
    ...(ctx.staleParents?.[cellId] ?? []),
  ]);
  pullCandidates.forEach((related) => {
    if (closure.has(related)) {
      return;
    }
    let shouldIncludeRelated = ctx.staleParents?.[cellId]?.includes(related);
    if (!shouldIncludeRelated) {
      for (const [executed, staleParents] of Object.entries(
        ctx.staleParentsByExecutedCellByChild?.[cellId] ?? {},
      )) {
        if (!closure.has(executed)) {
          continue;
        }
        shouldIncludeRelated = staleParents.includes(related);
        if (shouldIncludeRelated) {
          break;
        }
      }
    }
    if (shouldIncludeRelated) {
      closure.add(related);
      computeRawTransitiveClosureHelper(
        ctx,
        closure,
        related,
        edges,
        pullReactiveUpdates,
        true,
      );
    }
  });
  for (const [child, staleParents] of Object.entries(
    ctx.staleParentsByChildByExecutedCell?.[cellId] ?? {},
  )) {
    if (!closure.has(child)) {
      continue;
    }
    for (const parent of staleParents) {
      if (closure.has(parent) || !edges?.[child]?.includes(parent)) {
        continue;
      }
      closure.add(parent);
      computeRawTransitiveClosureHelper(
        ctx,
        closure,
        parent,
        edges,
        pullReactiveUpdates,
        true,
      );
    }
  }
}

export function computeRawTransitiveClosure(
  ctx: IClosureContext,
  startCellIds: string[],
  inclusive = true,
  parents = false,
): Set<string> {
  let cellIds = startCellIds;
  const closure = new Set(cellIds);
  while (true) {
    for (const cellId of cellIds) {
      computeRawTransitiveClosureHelper(
        ctx,
        closure,
        cellId,
        parents ? ctx.cellParents : ctx.cellChildren,
        false,
        true,
      );
    }
    if (parents || !(ctx.settings.pull_reactive_updates ?? false)) {
      break;
    }
    for (const cellId of closure) {
      computeRawTransitiveClosureHelper(
        ctx,
        closure,
        cellId,
        ctx.cellParents,
        true,
        true,
      );
    }
    if (
      cellIds.length === closure.size ||
      !(ctx.settings.push_reactive_updates_to_cousins ?? false)
    ) {
      break;
    }
    cellIds = Array.from(closure);
  }
  if (!inclusive) {
    for (const cellId of startCellIds) {
      closure.delete(cellId);
    }
  }
  return closure;
}

function computeTopoOrderIdxHelper(
  cellId: string,
  cellChildren: EdgeMap,
  isCodeCell: (id: string) => boolean,
  orderedCellIds: string[],
  seen: Set<string>,
): void {
  if (seen.has(cellId) || !isCodeCell(cellId)) {
    return;
  }
  seen.add(cellId);
  for (const child of cellChildren[cellId] ?? []) {
    computeTopoOrderIdxHelper(
      child,
      cellChildren,
      isCodeCell,
      orderedCellIds,
      seen,
    );
  }
  orderedCellIds.unshift(cellId);
}

/**
 * Topologically order the given cell ids by following the children edges, so
 * that a cell always appears before its descendants. Non-code cells are
 * skipped. Returns a map from cell id to its index in the ordering.
 */
export function computeTopoOrderIdx(
  cellIds: string[],
  cellChildren: EdgeMap,
  isCodeCell: (id: string) => boolean,
): { [cellId: string]: number } {
  const orderedCellIds: string[] = [];
  const seen = new Set<string>();
  for (const cellId of cellIds) {
    computeTopoOrderIdxHelper(
      cellId,
      cellChildren,
      isCodeCell,
      orderedCellIds,
      seen,
    );
  }
  const topoOrderIdx: { [cellId: string]: number } = {};
  orderedCellIds.forEach((cellId, idx) => {
    topoOrderIdx[cellId] = idx;
  });
  return topoOrderIdx;
}
