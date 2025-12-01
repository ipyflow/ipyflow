import { IComm, IShellFuture } from '@jupyterlab/services/lib/kernel/kernel';
import { Notebook } from '@jupyterlab/notebook';
import { ISessionContext } from '@jupyterlab/apputils';
import { Cell, CodeCell, ICellModel } from '@jupyterlab/cells';
import { KernelMessage } from '@jupyterlab/services';
import { JSONValue } from '@lumino/coreutils';

export type Highlights = 'all' | 'none' | 'executed' | 'reactive';

type CellMetadata = {
  index: number;
  content: string;
  type: string;
};

type CellMetadataMap = {
  [id: string]: CellMetadata;
};

// ipyflow frontend state
export class IpyflowSessionState {
  comm: IComm | null = null;
  safeSend: ((data: JSONValue) => void) | null = null;
  notebook: Notebook | null = null;
  session: ISessionContext | null = null;
  isIpyflowCommConnected = false;
  selectedCells: string[] = [];
  executedCells: Set<string> = new Set();
  dirtyCells: Set<string> = new Set();
  waitingCells: Set<string> = new Set();
  readyCells: Set<string> = new Set();
  waiterLinks: { [id: string]: string[] } = {};
  readyMakerLinks: { [id: string]: string[] } = {};
  staleParents: { [id: string]: string[] } = {};
  staleParentsByExecutedCellByChild: {
    [id: string]: { [id2: string]: string[] };
  } = {};
  staleParentsByChildByExecutedCell: {
    [id: string]: { [id2: string]: string[] };
  } = {};
  prevActiveCell: Cell<ICellModel> | null = null;
  activeCell: Cell<ICellModel> | null = null;
  cellsById: { [id: string]: Cell<ICellModel> } = {};
  orderIdxById: { [id: string]: number } = {};
  cellPendingExecution: CodeCell | null = null;
  isReactivelyExecuting = false;
  numAltModeExecutes = 0;
  altModeExecuteCells: Cell<ICellModel>[] | null = null;
  lastExecutionHighlights: Highlights | null = null;
  executedReactiveReadyCells: Set<string> = new Set();
  newReadyCells: Set<string> = new Set();
  forcedReactiveCells: Set<string> = new Set();
  forcedCascadingReactiveCells: Set<string> = new Set();
  numPendingForcedReactiveCounterBumps = 0;
  cellParents: { [id: string]: string[] } = {};
  cellChildren: { [id: string]: string[] } = {};
  settings: { [key: string]: string } = {};
  lastCellMetadataMap: CellMetadataMap | null = null;

  gatherCellMetadataAndContent() {
    const cell_metadata_by_id: CellMetadataMap = {};
    this.notebook.widgets.forEach((itercell, idx) => {
      const model = itercell.model;
      cell_metadata_by_id[model.id] = {
        index: idx,
        content: model.sharedModel.getSource(),
        type: model.type,
      };
    });
    return cell_metadata_by_id;
  }

  requestComputeExecSchedule() {
    (this.safeSend ?? this.comm.send)({
      type: 'compute_exec_schedule',
      cell_metadata_by_id: this.gatherCellMetadataAndContent(),
      is_reactively_executing: this.isReactivelyExecuting,
    });
  }

  isBatchReactive() {
    return (
      (this.isIpyflowCommConnected ?? false) &&
      this.settings?.exec_mode === 'reactive' &&
      this.settings?.reactivity_mode === 'batch'
    );
  }

  executeCells(cells: Cell<ICellModel>[]): void {
    if (cells.length === 0) {
      return;
    }
    let numFinished = 0;
    for (const cell of cells) {
      // if any of them fail, change the [*] to [ ] on subsequent cells
      CodeCell.execute(cell as CodeCell, this.session).then(() => {
        if (cell.promptNode.textContent?.includes('[*]')) {
          // can happen if a preceding cell errored
          cell.setPrompt('');
        } else {
          this.executedCells.add(cell.model.id);
        }
        if (++numFinished === cells.length) {
          // wait a tick first to allow the disk changes to propagate up
          this.isReactivelyExecuting = false;
          setTimeout(() => {
            this.requestComputeExecSchedule();
          }, 0);
        }
      });
    }
  }

  executeClosure(cells: Cell<ICellModel>[]) {
    if (cells.length === 0) {
      return;
    }
    const cellIds = cells.map((cell) => cell.model.id);
    const closureCells = this.computeTransitiveClosure(cellIds);
    this.executeCells(closureCells);
  }

  toggleReactivity(): IShellFuture<
    KernelMessage.IExecuteRequestMsg,
    KernelMessage.IExecuteReplyMsg
  > {
    if (this.settings.exec_mode === 'reactive') {
      this.settings.exec_mode = 'lazy';
    } else if (this.settings.exec_mode === 'lazy') {
      this.settings.exec_mode = 'reactive';
    }
    return this.session.session.kernel.requestExecute({
      code: '%flow toggle-reactivity',
      silent: true,
      store_history: false,
    });
  }

  bumpForcedReactiveCounter(): IShellFuture<
    KernelMessage.IExecuteRequestMsg,
    KernelMessage.IExecuteReplyMsg
  > {
    this.numPendingForcedReactiveCounterBumps--;
    return this.session.session.kernel.requestExecute({
      code: '%flow bump-min-forced-reactive-counter',
      silent: true,
      store_history: false,
    });
  }

  computeRawTransitiveClosureHelper(
    closure: Set<string>,
    cellId: string,
    edges: { [id: string]: string[] } | undefined | null,
    pullReactiveUpdates = false,
    skipFirstCheck = false
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
      this.computeRawTransitiveClosureHelper(
        closure,
        related,
        edges,
        pullReactiveUpdates
      );
    });
    if (
      pullReactiveUpdates &&
      (closure.size > prevClosureSize ||
        !this.executedCells.has(cellId) ||
        this.readyCells.has(cellId) ||
        this.waitingCells.has(cellId) ||
        this.dirtyCells.has(cellId))
    ) {
      closure.add(cellId);
    }
    if (!pullReactiveUpdates || !closure.has(cellId)) {
      return;
    }
    relatives.forEach((related) => {
      if (closure.has(related)) {
        return;
      }
      let shouldIncludeRelated = this.staleParents?.[cellId]?.includes(related);
      if (!shouldIncludeRelated) {
        for (const [executed, staleParents] of Object.entries(
          this.staleParentsByExecutedCellByChild?.[cellId] ?? {}
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
        this.computeRawTransitiveClosureHelper(
          closure,
          related,
          edges,
          pullReactiveUpdates,
          true
        );
      }
    });
    for (const [child, staleParents] of Object.entries(
      this.staleParentsByChildByExecutedCell?.[cellId] ?? {}
    )) {
      if (!closure.has(child)) {
        continue;
      }
      for (const parent of staleParents) {
        if (closure.has(parent) || !edges?.[child]?.includes(parent)) {
          continue;
        }
        closure.add(parent);
        this.computeRawTransitiveClosureHelper(
          closure,
          parent,
          edges,
          pullReactiveUpdates,
          true
        );
      }
    }
  }

  #computeTopoOrderIdxHelper(
    cellId: string,
    orderedCellIds: string[],
    seen: Set<string>
  ): void {
    if (seen.has(cellId) || this.cellsById[cellId]?.model?.type !== 'code') {
      return;
    }
    seen.add(cellId);
    for (const child of this.cellChildren[cellId] ?? []) {
      this.#computeTopoOrderIdxHelper(child, orderedCellIds, seen);
    }
    orderedCellIds.unshift(cellId);
  }

  #computeTopoOrderIdx(): { [cellId: string]: number } {
    const orderedCellIds: string[] = [];
    const seen = new Set<string>();
    for (const cellId of Object.keys(this.cellsById)) {
      this.#computeTopoOrderIdxHelper(cellId, orderedCellIds, seen);
    }
    const topoOrderIdx: { [cellId: string]: number } = {};
    orderedCellIds.forEach((cellId, idx) => {
      topoOrderIdx[cellId] = idx;
    });
    return topoOrderIdx;
  }

  cellIdsToCells(cellIds: string[]) {
    const orderIdxById =
      this.settings.flow_order === 'any_order'
        ? this.#computeTopoOrderIdx()
        : this.orderIdxById;
    return cellIds
      .filter((id) => this.cellsById[id] !== undefined)
      .filter((id) => this.orderIdxById[id] !== undefined)
      .sort((a, b) => orderIdxById[a] - orderIdxById[b])
      .map((id) => this.cellsById[id]);
  }

  computeRawTransitiveClosure(
    startCellIds: string[],
    inclusive = true,
    parents = false
  ): Set<string> {
    let cellIds = startCellIds;
    const closure = new Set(cellIds);
    while (true) {
      for (const cellId of cellIds) {
        if (parents) {
          this.computeRawTransitiveClosureHelper(
            closure,
            cellId,
            this.cellParents,
            false,
            true
          );
        } else {
          this.computeRawTransitiveClosureHelper(
            closure,
            cellId,
            this.cellChildren,
            false,
            true
          );
        }
      }
      if (parents || !(this.settings.pull_reactive_updates ?? false)) {
        break;
      }
      for (const cellId of closure) {
        this.computeRawTransitiveClosureHelper(
          closure,
          cellId,
          this.cellParents,
          true,
          true
        );
      }
      if (
        cellIds.length === closure.size ||
        !(this.settings.push_reactive_updates_to_cousins ?? false)
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

  computeTransitiveClosure(
    startCellIds: string[],
    inclusive = true,
    parents = false
  ): Cell<ICellModel>[] {
    return this.cellIdsToCells(
      Array.from(
        this.computeRawTransitiveClosure(startCellIds, inclusive, parents)
      )
    );
  }
}

type IpyflowState = {
  [session_id: string]: IpyflowSessionState;
};

export const ipyflowState: IpyflowState = {};

export function initSessionState(session_id: string): void {
  const ipyflowSessionState = new IpyflowSessionState();
  ipyflowState[session_id] = ipyflowSessionState;
  (window as any).ipyflow = ipyflowSessionState;
}

export function resetSessionState(session_id: string): void {
  delete ipyflowState[session_id];
}
