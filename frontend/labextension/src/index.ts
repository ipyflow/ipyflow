import type { IChangedArgs } from '@jupyterlab/coreutils/lib/interfaces';
import type {
  IComm,
  IShellFuture,
} from '@jupyterlab/services/lib/kernel/kernel';
import type {
  IObservableList,
  IObservableUndoableList,
} from '@jupyterlab/observables';
import type { KernelMessage } from '@jupyterlab/services';

import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin,
} from '@jupyterlab/application';

import { ICommandPalette, ISessionContext } from '@jupyterlab/apputils';

import { Cell, CodeCell, ICellModel, ICodeCellModel } from '@jupyterlab/cells';

import { INotebookTracker, Notebook } from '@jupyterlab/notebook';

import _ from 'lodash';

type Highlights = 'all' | 'none' | 'executed' | 'reactive';

const waitingClass = 'waiting-cell';
const readyClass = 'ready-cell';
const readyMakingClass = 'ready-making-cell';
const readyMakingInputClass = 'ready-making-input-cell';
const linkedWaitingClass = 'linked-waiting';
const linkedReadyMakerClass = 'linked-ready-maker';
const selfSliceClass = 'ipyflow-slice-self';
const directSliceClass = 'ipyflow-slice-direct';
const sliceClass = 'ipyflow-slice';

const cleanup = new Event('cleanup');

// ipyflow frontend state
class IpyflowSessionState {
  comm: IComm | null = null;
  notebook: Notebook | null = null;
  session: ISessionContext | null = null;
  isIpyflowCommConnected = false;
  dirtyCells: Set<string> = new Set();
  waitingCells: Set<string> = new Set();
  readyCells: Set<string> = new Set();
  waiterLinks: { [id: string]: string[] } = {};
  readyMakerLinks: { [id: string]: string[] } = {};
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
  cellParents: { [id: string]: string[] } = {};
  cellChildren: { [id: string]: string[] } = {};
  settings: { [key: string]: string } = {};

  gatherCellMetadataAndContent() {
    const cell_metadata_by_id: {
      [id: string]: {
        index: number;
        content: string;
        type: string;
      };
    } = {};
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
    this.comm.send({
      type: 'compute_exec_schedule',
      cell_metadata_by_id: this.gatherCellMetadataAndContent(),
      is_reactively_executing: this.isReactivelyExecuting,
    });
  }

  executeCells(cells: Cell<ICellModel>[]) {
    if (cells.length === 0) {
      return;
    }
    let numFinished = 0;
    for (const cell of cells) {
      // if any of them fail, change the [*] to [ ] on subsequent cells
      CodeCell.execute(cell as CodeCell, this.session).then((msg) => {
        if ((msg as any)?.content?.status === 'error') {
          numFinished = cells.length;
          for (const cell of cells) {
            if (cell.promptNode.textContent?.includes('[*]')) {
              cell.setPrompt('');
            }
          }
        } else {
          numFinished++;
        }
        if (numFinished === cells.length) {
          this.requestComputeExecSchedule();
        }
      });
    }
  }

  toggleReactivity(): IShellFuture<
    KernelMessage.IExecuteRequestMsg,
    KernelMessage.IExecuteReplyMsg
  > {
    if (this.settings.exec_mode === 'reactive') {
      this.settings.exec_mode = 'normal';
    } else if (this.settings.exec_mode === 'normal') {
      this.settings.exec_mode = 'reactive';
    }
    return this.session.session.kernel.requestExecute({
      code: '%flow toggle-reactivity',
      silent: true,
      store_history: false,
    });
  }

  computeTransitiveClosure(
    cellIds: string[],
    inclusive = true,
    parents = false
  ): Cell<ICellModel>[] {
    const closure = new Set<string>();
    for (const cellId of cellIds) {
      if (parents) {
        computeTransitiveClosureHelper(closure, cellId, this.cellParents);
      } else {
        computeTransitiveClosureHelper(closure, cellId, this.cellChildren);
      }
    }
    if (!inclusive) {
      for (const cellId of cellIds) {
        closure.delete(cellId);
      }
    }
    return Array.from(closure)
      .filter((id) => this.cellsById[id] !== undefined)
      .filter((id) => this.orderIdxById[id] !== undefined)
      .sort((a, b) => this.orderIdxById[a] - this.orderIdxById[b])
      .map((id) => this.cellsById[id]);
  }
}

type IpyflowState = {
  [session_id: string]: IpyflowSessionState;
};

const ipyflowState: IpyflowState = {};

function initSessionState(session_id: string): void {
  ipyflowState[session_id] = new IpyflowSessionState();
}

function resetSessionState(session_id: string): void {
  delete ipyflowState[session_id];
}

function computeTransitiveClosureHelper(
  closure: Set<string>,
  cellId: string,
  edges: { [id: string]: string[] }
): void {
  if (closure.has(cellId)) {
    return;
  }
  closure.add(cellId);
  const children = edges[cellId];
  if (children === undefined) {
    return;
  }
  children.forEach((child) =>
    computeTransitiveClosureHelper(closure, child, edges)
  );
}

/**
 * Initialization data for the jupyterlab-ipyflow extension.
 */
const extension: JupyterFrontEndPlugin<void> = {
  id: 'jupyterlab-ipyflow',
  requires: [INotebookTracker, ICommandPalette],
  autoStart: true,
  activate: (
    app: JupyterFrontEnd,
    notebooks: INotebookTracker,
    palette: ICommandPalette
  ) => {
    app.commands.addCommand('alt-mode-execute', {
      label: 'Alt Mode Execute',
      isEnabled: () => true,
      isVisible: () => true,
      isToggled: () => false,
      execute: () => {
        const session = notebooks.currentWidget.sessionContext;
        if (!session.isReady) {
          return;
        }
        app.commands.execute('notebook:enter-command-mode');
        const state: IpyflowSessionState = (ipyflowState[session.session.id] ??
          {}) as IpyflowSessionState;
        const altModeExecuteCells = state.altModeExecuteCells;
        state.altModeExecuteCells = null;
        if (!(state.isIpyflowCommConnected ?? false)) {
          CodeCell.execute(notebooks.activeCell as CodeCell, session);
          return;
        }
        if (
          state.settings.reactivity_mode !== 'batch' &&
          altModeExecuteCells !== null
        ) {
          return;
        }
        if (notebooks.activeCell.model.type !== 'code') {
          return;
        }
        state.numAltModeExecutes++;
        if (state.settings.reactivity_mode === 'incremental') {
          if (state.numAltModeExecutes === 1) {
            state
              .toggleReactivity()
              .done.then(() =>
                CodeCell.execute(notebooks.activeCell as CodeCell, session)
              );
          } else {
            CodeCell.execute(notebooks.activeCell as CodeCell, session);
          }
        } else if (state.settings.reactivity_mode === 'batch') {
          let closure = altModeExecuteCells ?? [notebooks.activeCell];
          if (
            state.settings.exec_mode === 'normal' &&
            altModeExecuteCells === null
          ) {
            closure = state.computeTransitiveClosure([
              notebooks.activeCell.model.id,
            ]);
          }
          if (state.numAltModeExecutes === 1) {
            state
              .toggleReactivity()
              .done.then(() => state.executeCells(closure));
          } else {
            state.executeCells(closure);
          }
        } else {
          console.error(
            `Unknown reactivity mode: ${state.settings.reactivity_mode}`
          );
        }
      },
    });
    app.commands.addKeyBinding({
      command: 'alt-mode-execute',
      keys: ['Accel Shift Enter'],
      selector: '.jp-Notebook',
    });
    app.commands.addKeyBinding({
      command: 'alt-mode-execute',
      keys: ['Ctrl Shift Enter'],
      selector: '.jp-Notebook',
    });
    palette.addItem({
      command: 'alt-mode-execute',
      category: 'execution',
      args: {},
    });

    const executeSlice = (isBackward: boolean) => {
      const session = notebooks.currentWidget.sessionContext;
      if (!session.isReady) {
        return;
      }
      const state: IpyflowSessionState = (ipyflowState[session.session.id] ??
        {}) as IpyflowSessionState;
      if (!(state.isIpyflowCommConnected ?? false)) {
        return;
      }
      app.commands.execute('notebook:enter-command-mode');
      const closure = state.computeTransitiveClosure(
        [state.activeCell.model.id],
        true,
        isBackward
      );
      // TODO: prevent forced (cascading) reactive cells from running if not part of slice?
      if (state.settings.exec_mode === 'normal') {
        state.executeCells(closure);
      } else {
        state.altModeExecuteCells = closure;
        app.commands.execute('alt-mode-execute');
      }
    };

    app.commands.addCommand('execute-forward-slice', {
      label: 'Execute Forward Slice',
      isEnabled: () => true,
      isVisible: () => true,
      isToggled: () => false,
      execute: () => executeSlice(false),
    });
    app.commands.addKeyBinding({
      command: 'execute-forward-slice',
      keys: ['Accel J'],
      selector: '.jp-Notebook',
    });
    app.commands.addKeyBinding({
      command: 'execute-forward-slice',
      keys: ['Accel ArrowDown'],
      selector: '.jp-Notebook',
    });

    app.commands.addCommand('execute-backward-slice', {
      label: 'Execute Backward Slice',
      isEnabled: () => true,
      isVisible: () => true,
      isToggled: () => false,
      execute: () => executeSlice(true),
    });
    app.commands.addKeyBinding({
      command: 'execute-backward-slice',
      keys: ['Accel K'],
      selector: '.jp-Notebook',
    });
    app.commands.addKeyBinding({
      command: 'execute-backward-slice',
      keys: ['Accel ArrowUp'],
      selector: '.jp-Notebook',
    });

    const executeRemaining = () => {
      const session = notebooks.currentWidget.sessionContext;
      if (!session.isReady) {
        return;
      }
      const state: IpyflowSessionState = (ipyflowState[session.session.id] ??
        {}) as IpyflowSessionState;
      if (state.isIpyflowCommConnected ?? false) {
        const closure = state.computeTransitiveClosure(
          [state.activeCell.model.id],
          false
        );
        if (closure.length > 0) {
          state.executeCells(closure);
        } else {
          state.requestComputeExecSchedule();
        }
      }
    };

    app.commands.commandExecuted.connect((_, args) => {
      const session = notebooks.currentWidget.sessionContext;
      if (!session.isReady) {
        return;
      }
      const state: IpyflowSessionState = (ipyflowState[session.session.id] ??
        {}) as IpyflowSessionState;
      if (!(state.isIpyflowCommConnected ?? false)) {
        return;
      }
      const settings = state.settings;
      const isBatch = settings?.reactivity_mode === 'batch';
      const isReactive = settings?.exec_mode === 'reactive';
      if (!isBatch) {
        return;
      }
      if (args.id === 'notebook:run-cell') {
        if (isReactive) {
          executeRemaining();
        } else {
          state.requestComputeExecSchedule();
        }
      } else if (args.id === 'notebook:run-cell-and-select-next') {
        const origActiveCell = state.activeCell;
        try {
          state.activeCell = state.prevActiveCell;
          if (isReactive) {
            executeRemaining();
          } else {
            state.requestComputeExecSchedule();
          }
        } finally {
          state.activeCell = origActiveCell;
        }
      }
    });
    notebooks.widgetAdded.connect((sender, nbPanel) => {
      const session = nbPanel.sessionContext;
      let commDisconnectHandler = () => resetSessionState(session.session.id);

      const registerCommTarget = () => {
        session.session.kernel.registerCommTarget(
          'ipyflow-client',
          (comm, _open_msg) => {
            comm.onMsg = (msg) => {
              const payload = msg.content.data;
              if (!(payload.success ?? true)) {
                return;
              }
              if (payload.type === 'unestablish') {
                commDisconnectHandler();
              } else if (payload.type === 'establish') {
                commDisconnectHandler();
                commDisconnectHandler = connectToComm(session, nbPanel.content);
              }
            };
            commDisconnectHandler();
            commDisconnectHandler = connectToComm(session, nbPanel.content);
          }
        );
      };

      session.ready.then(() => {
        clearCellState(nbPanel.content);
        registerCommTarget();
        commDisconnectHandler();
        commDisconnectHandler = connectToComm(session, nbPanel.content);
        session.kernelChanged.connect((_, args) => {
          if (args.newValue == null) {
            return;
          }
          clearCellState(nbPanel.content);
          commDisconnectHandler();
          resetSessionState(session.session.id);
          commDisconnectHandler = () => resetSessionState(session.session.id);
          session.ready.then(() => {
            registerCommTarget();
            commDisconnectHandler = connectToComm(session, nbPanel.content);
          });
        });
      });
    });
  },
};

const getJpInputCollapser = (elem: HTMLElement) => {
  if (elem === null || elem === undefined) {
    return null;
  }
  const child = elem.children.item(1);
  if (child === null) {
    return null;
  }
  return child.firstElementChild;
};

const getJpOutputCollapser = (elem: HTMLElement) => {
  if (elem === null || elem === undefined) {
    return null;
  }
  const child = elem.children.item(2);
  if (child === null) {
    return null;
  }
  return child.firstElementChild;
};

const attachCleanupListener = (
  elem: Element,
  evt: 'mouseover' | 'mouseout',
  listener: any
) => {
  const cleanupListener = () => {
    elem.removeEventListener(evt, listener);
    elem.removeEventListener('cleanup', cleanupListener);
  };
  elem.addEventListener(evt, listener);
  elem.addEventListener('cleanup', cleanupListener);
};

const addWaitingOutputInteraction = (
  elem: Element,
  linkedElem: Element,
  evt: 'mouseover' | 'mouseout',
  add_or_remove: 'add' | 'remove',
  css: string
) => {
  if (elem === null || linkedElem === null) {
    return;
  }
  const listener = () => {
    linkedElem.firstElementChild.classList[add_or_remove](css);
  };
  attachCleanupListener(elem, evt, listener);
};

const addWaitingOutputInteractions = (
  elem: HTMLElement,
  linkedInputClass: string
) => {
  addWaitingOutputInteraction(
    getJpInputCollapser(elem),
    getJpOutputCollapser(elem),
    'mouseover',
    'add',
    linkedWaitingClass
  );
  addWaitingOutputInteraction(
    getJpInputCollapser(elem),
    getJpOutputCollapser(elem),
    'mouseout',
    'remove',
    linkedWaitingClass
  );

  addWaitingOutputInteraction(
    getJpOutputCollapser(elem),
    getJpInputCollapser(elem),
    'mouseover',
    'add',
    linkedInputClass
  );
  addWaitingOutputInteraction(
    getJpOutputCollapser(elem),
    getJpInputCollapser(elem),
    'mouseout',
    'remove',
    linkedInputClass
  );
};

const clearCellState = (notebook: Notebook) => {
  notebook.widgets.forEach((cell) => {
    cell.node.classList.remove(waitingClass);
    cell.node.classList.remove(readyMakingClass);
    cell.node.classList.remove(readyClass);
    cell.node.classList.remove(readyMakingInputClass);
    cell.node.classList.remove(selfSliceClass);
    cell.node.classList.remove(directSliceClass);
    cell.node.classList.remove(sliceClass);

    // clear any old event listeners
    const inputCollapser = getJpInputCollapser(cell.node);
    if (inputCollapser !== null) {
      inputCollapser.firstElementChild.classList.remove(linkedWaitingClass);
      inputCollapser.firstElementChild.classList.remove(linkedReadyMakerClass);
      inputCollapser.dispatchEvent(cleanup);
    }

    const outputCollapser = getJpOutputCollapser(cell.node);
    if (outputCollapser !== null) {
      outputCollapser.firstElementChild.classList.remove(linkedWaitingClass);
      outputCollapser.firstElementChild.classList.remove(linkedReadyMakerClass);
      outputCollapser.dispatchEvent(cleanup);
    }
  });
};

const addUnsafeCellInteraction = (
  elem: Element,
  linkedElems: string[],
  cellsById: { [id: string]: Cell },
  collapserFun: (elem: HTMLElement) => Element,
  evt: 'mouseover' | 'mouseout',
  add_or_remove: 'add' | 'remove',
  waitingCells: Set<string>
) => {
  if (elem === null) {
    return;
  }
  const listener = () => {
    for (const linkedId of linkedElems) {
      let css = linkedReadyMakerClass;
      if (waitingCells.has(linkedId)) {
        css = linkedWaitingClass;
      }
      const collapser = collapserFun(cellsById[linkedId].node);
      if (collapser === null || collapser.firstElementChild === null) {
        return;
      }
      collapser.firstElementChild.classList[add_or_remove](css);
    }
  };
  elem.addEventListener(evt, listener);
  attachCleanupListener(elem, evt, listener);
};

const connectToComm = (session: ISessionContext, notebook: Notebook) => {
  initSessionState(session.session.id);
  const state = ipyflowState[session.session.id];
  state.activeCell = notebook.activeCell;
  const comm = session.session.kernel.createComm('ipyflow', 'ipyflow');
  state.comm = comm;
  state.notebook = notebook;
  state.session = session;
  let disconnected = false;

  const syncDirtiness = (cell: Cell<ICellModel>) => {
    if (cell !== null && cell.model !== null) {
      if ((<ICodeCellModel>cell.model).isDirty) {
        state.dirtyCells.add(cell.model.id);
      } else {
        state.dirtyCells.delete(cell.model.id);
      }
    }
  };

  const onContentChanged = _.debounce(() => {
    if (disconnected) {
      notebook.model.contentChanged.disconnect(onContentChanged);
      return;
    }
    notebook.widgets.forEach(syncDirtiness);
    comm.send({
      type: 'notify_content_changed',
      cell_metadata_by_id: state.gatherCellMetadataAndContent(),
    });
  }, 500);

  const onExecution = (cell: ICellModel, args: IChangedArgs<any>) => {
    if (disconnected) {
      cell.stateChanged.disconnect(onExecution);
      return;
    }
    if (args.name !== 'executionCount' || args.newValue === null) {
      return;
    }
    state.dirtyCells.delete(cell.id);
    notebook.widgets.forEach((itercell) => {
      if (itercell.model.id === cell.id) {
        itercell.node.classList.remove(readyClass);
        itercell.node.classList.remove(readyMakingInputClass);
      }
    });
    if (state.settings.reactivity_mode === 'incremental') {
      state.requestComputeExecSchedule();
    }
  };

  for (const cell of notebook.widgets) {
    cell.model.stateChanged.connect(onExecution);
  }

  const onCellsAdded = (
    _: IObservableUndoableList<ICellModel>,
    change: IObservableList.IChangedArgs<ICellModel>
  ) => {
    if (disconnected) {
      notebook.model.cells.changed.disconnect(onCellsAdded);
      return;
    }
    if (change.type === 'add') {
      for (const cell of change.newValues) {
        cell?.stateChanged.connect(onExecution);
      }
    } else if (change.type === 'remove') {
      for (const cell of change.oldValues) {
        cell?.stateChanged.disconnect(onExecution);
      }
    }
  };
  notebook.model.cells.changed.connect(onCellsAdded);

  const notifyActiveCell = (newActiveCell: ICellModel) => {
    let newActiveCellOrderIdx = -1;
    notebook.widgets.forEach((itercell, idx) => {
      if (itercell.model.id === newActiveCell.id) {
        newActiveCellOrderIdx = idx;
      }
    });
    const payload = {
      type: 'change_active_cell',
      active_cell_id: newActiveCell.id,
      active_cell_order_idx: newActiveCellOrderIdx,
    };
    comm.send(payload);
  };

  const refreshNodeMapping = (notebook: Notebook) => {
    state.cellsById = {};
    state.orderIdxById = {};

    notebook.widgets.forEach((cell, idx) => {
      state.cellsById[cell.model.id] = cell;
      state.orderIdxById[cell.model.id] = idx;
    });
  };

  const onActiveCellChange = (nb: Notebook, cell: Cell<ICellModel>) => {
    if (notebook !== nb) {
      return;
    }
    if (disconnected) {
      notebook.activeCellChanged.disconnect(onActiveCellChange);
      return;
    }
    notifyActiveCell(cell.model);
    state.prevActiveCell = state.activeCell;
    state.activeCell = cell;

    if (
      state.activeCell === null ||
      state.activeCell.model === null ||
      state.activeCell.model.type !== 'code'
    ) {
      return;
    }

    notifyActiveCell(state.activeCell.model);

    if (state.dirtyCells.has(state.activeCell.model.id)) {
      (state.activeCell.model as any)._setDirty?.(true);
    }
    updateUI(notebook);
  };

  const actionUpdatePairs: {
    action: 'mouseover' | 'mouseout';
    update: 'add' | 'remove';
  }[] = [
    {
      action: 'mouseover',
      update: 'add',
    },
    {
      action: 'mouseout',
      update: 'remove',
    },
  ];

  const updateOneCellUI = (
    id: string,
    isSelf: boolean,
    inDirectSlice: boolean,
    inSlice: boolean,
    showCollapserHighlights: boolean
  ) => {
    const model = state.cellsById[id].model;
    if (model.type !== 'code') {
      return;
    }
    const codeModel = model as ICodeCellModel;
    if (codeModel.executionCount == null) {
      return;
    }
    const elem = state.cellsById[id].node;
    if (isSelf) {
      elem.classList.add(selfSliceClass);
    } else {
      elem.classList.remove(selfSliceClass);
    }
    if (inDirectSlice && !isSelf) {
      elem.classList.add(directSliceClass);
    } else {
      elem.classList.remove(directSliceClass);
    }
    if (inSlice && !inDirectSlice && !isSelf) {
      elem.classList.add(sliceClass);
    } else {
      elem.classList.remove(sliceClass);
    }
    if (!showCollapserHighlights) {
      return;
    }
    if (state.waitingCells.has(id)) {
      elem.classList.add(waitingClass);
      elem.classList.add(readyClass);
      elem.classList.remove(readyMakingInputClass);
      addWaitingOutputInteractions(elem, linkedWaitingClass);
    } else if (state.readyCells.has(id)) {
      elem.classList.add(readyMakingInputClass);
      elem.classList.add(readyClass);
      addWaitingOutputInteractions(elem, linkedReadyMakerClass);
    }

    if (state.settings.exec_mode === 'reactive') {
      return;
    }

    if (state.waiterLinks[id] !== undefined) {
      actionUpdatePairs.forEach(({ action, update }) => {
        addUnsafeCellInteraction(
          getJpInputCollapser(elem),
          state.waiterLinks[id],
          state.cellsById,
          getJpInputCollapser,
          action,
          update,
          state.waitingCells
        );

        addUnsafeCellInteraction(
          getJpOutputCollapser(elem),
          state.waiterLinks[id],
          state.cellsById,
          getJpInputCollapser,
          action,
          update,
          state.waitingCells
        );
      });
    }

    if (state.readyMakerLinks[id] !== undefined) {
      if (!state.waitingCells.has(id)) {
        elem.classList.add(readyMakingClass);
        elem.classList.add(readyClass);
      }
      actionUpdatePairs.forEach(({ action, update }) => {
        addUnsafeCellInteraction(
          getJpInputCollapser(elem),
          state.readyMakerLinks[id],
          state.cellsById,
          getJpInputCollapser,
          action,
          update,
          state.waitingCells
        );

        addUnsafeCellInteraction(
          getJpInputCollapser(elem),
          state.readyMakerLinks[id],
          state.cellsById,
          getJpOutputCollapser,
          action,
          update,
          state.waitingCells
        );
      });
    }
  };

  const updateUI = (notebook: Notebook) => {
    clearCellState(notebook);
    refreshNodeMapping(notebook);
    const slice = new Set<string>();
    let directSlice = new Set<string>();
    const activeCellId = state.activeCell.model.id;
    if (
      state.cellChildren[activeCellId] !== undefined ||
      state.cellParents[activeCellId] !== undefined
    ) {
      directSlice = new Set([
        activeCellId,
        ...state.cellChildren[activeCellId],
        ...state.cellParents[activeCellId],
      ]);
      computeTransitiveClosureHelper(slice, activeCellId, state.cellChildren);
      slice.delete(activeCellId);
      computeTransitiveClosureHelper(slice, activeCellId, state.cellParents);
    }
    for (const [id] of Object.entries(state.cellsById)) {
      updateOneCellUI(
        id,
        id === activeCellId && directSlice.has(id),
        directSlice.has(id),
        slice.has(id),
        state.lastExecutionHighlights !== 'none'
      );
    }
  };

  comm.onMsg = (msg) => {
    const payload = msg.content.data;
    if (disconnected || !(payload.success ?? true)) {
      return;
    }
    if (payload.type === 'establish') {
      state.isIpyflowCommConnected = true;
      notebook.activeCellChanged.connect(onActiveCellChange);
      notebook.activeCell.model.stateChanged.connect(onExecution);
      notifyActiveCell(notebook.activeCell.model);
      notebook.model.contentChanged.connect(onContentChanged);
      state.requestComputeExecSchedule();
    } else if (payload.type === 'set_exec_mode') {
      state.numAltModeExecutes = 0;
      state.settings.exec_mode = payload.exec_mode as string;
    } else if (payload.type === 'compute_exec_schedule') {
      state.settings = payload.settings as { [key: string]: string };
      state.cellParents = payload.cell_parents as { [id: string]: string[] };
      state.cellChildren = payload.cell_children as { [id: string]: string[] };
      state.waitingCells = new Set(payload.waiting_cells as string[]);
      state.readyCells = new Set(payload.ready_cells as string[]);
      state.forcedReactiveCells = new Set([
        ...state.forcedReactiveCells,
        ...(payload.forced_reactive_cells as string[]),
      ]);
      state.forcedCascadingReactiveCells = new Set([
        ...state.forcedCascadingReactiveCells,
        ...(payload.forced_cascading_reactive_cells as string[]),
      ]);
      state.waiterLinks = payload.waiter_links as { [id: string]: string[] };
      state.readyMakerLinks = payload.ready_maker_links as {
        [id: string]: string[];
      };
      state.cellPendingExecution = null;
      const exec_mode = payload.exec_mode as string;
      state.isReactivelyExecuting =
        state.isReactivelyExecuting ||
        ((payload?.is_reactively_executing as boolean) ?? false) ||
        exec_mode === 'reactive';
      if (exec_mode === 'reactive') {
        state.newReadyCells = new Set([
          ...state.newReadyCells,
          ...(payload.new_ready_cells as string[]),
        ]);
      } else {
        state.newReadyCells.clear();
      }
      const flow_order = payload.flow_order;
      const exec_schedule = payload.exec_schedule;
      state.lastExecutionHighlights = payload.highlights as Highlights;
      const lastExecutedCellId = payload.last_executed_cell_id as string;
      state.executedReactiveReadyCells.add(lastExecutedCellId);
      const last_execution_was_error =
        payload.last_execution_was_error as boolean;
      let doneReactivelyExecuting = false;
      if (last_execution_was_error) {
        doneReactivelyExecuting = true;
      } else if (state.settings.reactivity_mode === 'batch') {
        const cascadingReactiveCellIds = state
          .computeTransitiveClosure(
            Array.from(state.forcedCascadingReactiveCells).filter(
              (id) => !state.executedReactiveReadyCells.has(id)
            )
          )
          .map((cell) => cell.model.id);
        let reactiveCells: Array<Cell<ICellModel>>;
        if (exec_mode === 'reactive') {
          reactiveCells = state
            .computeTransitiveClosure(
              [
                ...state.newReadyCells,
                ...state.forcedReactiveCells,
                ...cascadingReactiveCellIds,
              ].filter((id) => !state.executedReactiveReadyCells.has(id))
            )
            .filter(
              (cell) => !state.executedReactiveReadyCells.has(cell.model.id)
            );
        } else {
          reactiveCells = [
            ...state.forcedReactiveCells,
            ...cascadingReactiveCellIds,
          ]
            .filter(
              (id) =>
                !state.executedReactiveReadyCells.has(id) &&
                state.cellsById[id] !== undefined &&
                state.orderIdxById[id] !== undefined
            )
            .sort((a, b) => state.orderIdxById[a] - state.orderIdxById[b])
            .map((id) => state.cellsById[id]);
        }
        if (reactiveCells.length === 0) {
          doneReactivelyExecuting = true;
        } else {
          state.isReactivelyExecuting = true;
          state.executedReactiveReadyCells = new Set([
            ...state.executedReactiveReadyCells,
            ...reactiveCells.map((cell) => cell.model.id),
          ]);
          state.executeCells(reactiveCells);
        }
      } else if (state.settings.reactivity_mode === 'incremental') {
        let lastExecutedCellIdSeen = false;
        for (const cell of notebook.widgets) {
          if (!lastExecutedCellIdSeen) {
            lastExecutedCellIdSeen = cell.model.id === lastExecutedCellId;
            if (flow_order === 'in_order' || exec_schedule === 'strict') {
              continue;
            }
          }
          if (
            cell.model.type !== 'code' ||
            state.executedReactiveReadyCells.has(cell.model.id)
          ) {
            continue;
          }
          if (!state.forcedReactiveCells.has(cell.model.id)) {
            if (
              !state.newReadyCells.has(cell.model.id) ||
              exec_mode !== 'reactive'
            ) {
              continue;
            }
          }
          const codeCell = cell as CodeCell;
          if (state.cellPendingExecution === null) {
            state.cellPendingExecution = codeCell;
            // break early if using one of the order-based semantics
            if (flow_order === 'in_order' || exec_schedule === 'strict') {
              break;
            }
          } else if (codeCell.model.executionCount == null) {
            // pass
          } else if (
            codeCell.model.executionCount <
            state.cellPendingExecution.model.executionCount
          ) {
            // otherwise, execute in order of earliest execution counter
            state.cellPendingExecution = codeCell;
          }
        }
        if (state.cellPendingExecution === null) {
          doneReactivelyExecuting = true;
        } else {
          state.isReactivelyExecuting = true;
          CodeCell.execute(state.cellPendingExecution, session);
        }
      }
      if (doneReactivelyExecuting) {
        if (state.isReactivelyExecuting) {
          if (state.lastExecutionHighlights === 'reactive') {
            state.readyCells = state.executedReactiveReadyCells;
          }
          comm.send({
            type: 'reactivity_cleanup',
          });
        }
        if (state.numAltModeExecutes > 0 && --state.numAltModeExecutes === 0) {
          state.toggleReactivity();
        }
        state.forcedReactiveCells = new Set();
        state.forcedCascadingReactiveCells = new Set();
        state.newReadyCells = new Set();
        state.executedReactiveReadyCells = new Set();
        state.isReactivelyExecuting = false;
        updateUI(notebook);
      }
    }
  };
  comm.open({
    interface: 'jupyterlab',
  });
  // return a disconnection handle
  return () => {
    comm.dispose();
    disconnected = true;
    state.isIpyflowCommConnected = false;
    resetSessionState(session.session.id);
  };
};

export default extension;
