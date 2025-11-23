import type { IChangedArgs } from '@jupyterlab/coreutils/lib/interfaces';
import type { IObservableList } from '@jupyterlab/observables';
import { JupyterFrontEnd, JupyterFrontEndPlugin } from '@jupyterlab/application';
import { ICommandPalette, ISessionContext } from '@jupyterlab/apputils';
import { Cell, CodeCell, ICellModel, ICodeCellModel } from '@jupyterlab/cells';
import { type CellList, INotebookTracker, Notebook } from '@jupyterlab/notebook';
import { debounce, isEqual } from 'lodash';

import {
  classicColorsClass,
  executeSliceClass,
  linkedReadyMakerClass,
  linkedWaitingClass,
  readyClass,
  readyMakingClass,
  readyMakingInputClass,
  sliceClass,
  waitingClass,
} from './classes';
import {
  addUnsafeCellInteraction,
  addWaitingOutputInteractions,
  clearCellState,
  getJpInputCollapser,
  getJpOutputCollapser,
} from './dom';
import {
  type Highlights,
  type IpyflowSessionState,
  initSessionState,
  ipyflowState,
  resetSessionState,
} from './store';
import { mergeMaps } from './utils';

const deferredCells: Cell<ICellModel>[] = [];

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
    app.commands.addCommand('execute-stale', {
      label: 'Execute Ready Cells',
      isEnabled: () => true,
      isVisible: () => true,
      isToggled: () => false,
      execute: () => {
        const session = notebooks.currentWidget.sessionContext;
        if (!session.isReady) {
          return;
        }
        const state: IpyflowSessionState = (ipyflowState[session.session.id] ??
          {}) as IpyflowSessionState;
        if (!(state.isIpyflowCommConnected ?? false)) {
          return;
        }
        const cellIdsToExecute = Array.from(
          new Set([...state.dirtyCells, ...state.readyCells])
        );
        let cellsToExecute;
        if (state.settings.reactivity_mode === 'batch') {
          cellsToExecute = state.computeTransitiveClosure(cellIdsToExecute);
        } else {
          cellsToExecute = state.cellIdsToCells(cellIdsToExecute);
        }
        state.executeCells(cellsToExecute);
      },
    });
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
          state.executedCells.add(notebooks.activeCell.model.id);
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
            state.toggleReactivity().done.then(() => {
              state.executedCells.add(notebooks.activeCell.model.id);
              CodeCell.execute(notebooks.activeCell as CodeCell, session);
            });
          } else {
            state.executedCells.add(notebooks.activeCell.model.id);
            CodeCell.execute(notebooks.activeCell as CodeCell, session);
          }
        } else if (state.settings.reactivity_mode === 'batch') {
          let closure = altModeExecuteCells ?? [notebooks.activeCell];
          if (
            state.settings.exec_mode === 'lazy' &&
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
    app.commands.addKeyBinding({
      command: 'execute-stale',
      keys: ['Space'],
      selector: '.jp-Notebook.jp-mod-commandMode',
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
      state.numPendingForcedReactiveCounterBumps++;
      if (state.settings.exec_mode === 'lazy') {
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

    let runCellCommand: any;
    let runCellAndSelectNextCommand: any;
    let runMenuRunCommand: any;
    try {
      runCellCommand = (app.commands as any)._commands.get('notebook:run-cell');
      runCellAndSelectNextCommand = (app.commands as any)._commands.get(
        'notebook:run-cell-and-select-next'
      );
      runMenuRunCommand = (app.commands as any)._commands.get('runmenu:run');
    } catch {
      runCellCommand = (app.commands as any)._commands['notebook:run-cell'];
      runCellAndSelectNextCommand = (app.commands as any)._commands[
        'notebook:run-cell-and-select-next'
      ];
      runMenuRunCommand = (app.commands as any)._commands['runmenu:run'];
    }
    const runCellCommandExecute = runCellCommand.execute;
    const runCellAndSelectNextCommandExecute =
      runCellAndSelectNextCommand.execute;
    const runMenuRunCommandExecute = runMenuRunCommand.execute;

    const getIpyflowState = () => {
      const session = notebooks.currentWidget.sessionContext;
      if (!session.isReady) {
        return {} as IpyflowSessionState;
      }
      return (ipyflowState[session.session.id] ?? {}) as IpyflowSessionState;
    };

    const isBatchReactive = () => {
      const state = getIpyflowState();
      return state.isBatchReactive();
    };

    [
      [runCellCommand, runCellCommandExecute, 'notebook:run-cell'],
      [
        runCellAndSelectNextCommand,
        runCellAndSelectNextCommandExecute,
        'notebook:run-cell-and-select-next',
      ],
      [runMenuRunCommand, runMenuRunCommandExecute, 'runmenu:run'],
    ].forEach(([command, exec, commandId]) => {
      command.execute = (...args: any[]) => {
        const state = getIpyflowState();
        const nbpanel = notebooks.currentWidget;
        const notebook = nbpanel.content;
        const kernel = nbpanel.sessionContext.session.kernel.name;
        if (kernel === 'ipyflow' && !(state?.isIpyflowCommConnected ?? false)) {
          for (const cell of notebook.widgets) {
            if (notebook.isSelectedOrActive(cell)) {
              cell.setPrompt('*');
              deferredCells.push(cell);
            }
          }
        } else if (
          isBatchReactive() &&
          state?.activeCell?.model?.type === 'code'
        ) {
          app.commands.execute('notebook:enter-command-mode');
          const lastCell =
            state.notebook.widgets[state.notebook.widgets.length - 1];
          const isExecutingLastCell =
            state.activeCell.model.id === lastCell.model.id;
          executeBatchReactive();
          if (
            ['notebook:run-cell-and-select-next', 'runmenu:run'].includes(
              commandId
            )
          ) {
            if (isExecutingLastCell) {
              app.commands.execute('notebook:insert-cell-below');
            } else {
              app.commands.execute('notebook:move-cursor-down');
            }
          }
        } else {
          exec.call(command, args);
          state.requestComputeExecSchedule();
        }
      };
    });

    const executeBatchReactive = (skipFirst = false) => {
      const state = getIpyflowState();
      if (state.isIpyflowCommConnected ?? false) {
        const closureCellIds: string[] = [];
        for (const cell of state.notebook.widgets) {
          if (state.notebook.isSelectedOrActive(cell)) {
            closureCellIds.push(cell.model.id);
          }
        }
        let closure = state.computeTransitiveClosure(closureCellIds, true);
        if (skipFirst) {
          closure = closure.splice(1);
        }
        if (closure.length > 0) {
          state.executeCells(closure);
        } else {
          state.requestComputeExecSchedule();
        }
      }
    };

    notebooks.currentChanged.connect((_, nbPanel) => {
      const session = nbPanel.sessionContext;
      if (session?.session == null) {
        delete (window as any).ipyflow;
      } else {
        (window as any).ipyflow = ipyflowState[session.session.id];
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
                commDisconnectHandler = connectToComm(
                  session,
                  notebooks,
                  nbPanel.content
                );
              }
            };
            commDisconnectHandler();
            commDisconnectHandler = connectToComm(
              session,
              notebooks,
              nbPanel.content
            );
          }
        );
      };

      session.ready.then(() => {
        clearCellState(nbPanel.content);
        registerCommTarget();
        commDisconnectHandler();
        commDisconnectHandler = connectToComm(
          session,
          notebooks,
          nbPanel.content
        );
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
            commDisconnectHandler = connectToComm(
              session,
              notebooks,
              nbPanel.content
            );
          });
        });
      });
    });
  },
};

const connectToComm = (
  session: ISessionContext,
  notebooks: INotebookTracker,
  notebook: Notebook
) => {
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

  const onContentChanged = debounce(() => {
    if (disconnected) {
      notebook.model.contentChanged.disconnect(onContentChanged);
      notebook.model.cells.changed.disconnect(onContentChanged);
      return;
    }
    const cell_metadata_by_id = state.gatherCellMetadataAndContent();
    if (isEqual(cell_metadata_by_id, state.lastCellMetadataMap)) {
      // fixes https://github.com/ipyflow/ipyflow/issues/145
      return;
    }
    state.lastCellMetadataMap = cell_metadata_by_id;
    notebook.widgets.forEach(syncDirtiness);
    comm.send({
      type: 'notify_content_changed',
      cell_metadata_by_id,
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
    state.executedCells.add(cell.id);
    state.dirtyCells.delete(cell.id);
    notebook.widgets.forEach((itercell) => {
      if (itercell.model.id === cell.id) {
        itercell.node.classList.remove(readyClass);
        itercell.node.classList.remove(readyMakingInputClass);
      }
    });
  };

  for (const cell of notebook.widgets) {
    cell.model.stateChanged.connect(onExecution);
  }

  const onCellsAdded = (
    _cells: CellList,
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
    if (newActiveCell.id == null) {
      return;
    }
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
    cell: Cell<ICellModel>,
    inSlice: boolean,
    inExecuteSlice: boolean,
    showCollapserHighlights: boolean
  ) => {
    const { model, node } = cell;
    const id = model.id;
    if (model.type !== 'code') {
      return;
    }
    if ((state.settings.color_scheme ?? 'lazy') === 'classic') {
      node.classList.add(classicColorsClass);
    }
    if (inExecuteSlice) {
      node.classList.add(executeSliceClass);
    } else {
      node.classList.remove(executeSliceClass);
    }
    if (inSlice && !inExecuteSlice) {
      node.classList.add(sliceClass);
    } else {
      node.classList.remove(sliceClass);
    }
    if (!showCollapserHighlights) {
      return;
    }
    if (state.waitingCells.has(id)) {
      node.classList.add(waitingClass);
      node.classList.add(readyClass);
      node.classList.remove(readyMakingInputClass);
      addWaitingOutputInteractions(node, linkedWaitingClass);
    } else if (state.readyCells.has(id)) {
      node.classList.add(readyMakingInputClass);
      node.classList.add(readyClass);
      addWaitingOutputInteractions(node, linkedReadyMakerClass);
    }

    if (state.settings.exec_mode === 'reactive') {
      return;
    }

    if (state.waiterLinks[id] !== undefined) {
      actionUpdatePairs.forEach(({ action, update }) => {
        addUnsafeCellInteraction(
          getJpInputCollapser(node),
          state.waiterLinks[id],
          state.cellsById,
          getJpInputCollapser,
          action,
          update,
          state.waitingCells
        );

        addUnsafeCellInteraction(
          getJpOutputCollapser(node),
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
        node.classList.add(readyMakingClass);
        node.classList.add(readyClass);
      }
      actionUpdatePairs.forEach(({ action, update }) => {
        addUnsafeCellInteraction(
          getJpInputCollapser(node),
          state.readyMakerLinks[id],
          state.cellsById,
          getJpInputCollapser,
          action,
          update,
          state.waitingCells
        );

        addUnsafeCellInteraction(
          getJpInputCollapser(node),
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
    let closureCellIds = state.selectedCells;
    if (closureCellIds.length === 0) {
      closureCellIds = [state.activeCell.model.id];
    }
    const executeSlice = state.computeRawTransitiveClosure(
      closureCellIds,
      true,
      false
    );
    closureCellIds = Array.from(executeSlice);
    const slice = new Set(executeSlice);
    for (const cellId of closureCellIds) {
      slice.delete(cellId);
      state.computeRawTransitiveClosureHelper(slice, cellId, state.cellParents);
    }
    for (const cell of notebook.widgets) {
      const id = cell.model.id;
      updateOneCellUI(
        cell,
        slice.has(id),
        executeSlice.has(id),
        state.lastExecutionHighlights !== 'none'
      );
    }
  };

  const onSelectionChanged = () => {
    if (disconnected) {
      notebooks.selectionChanged.disconnect(onSelectionChanged);
    }
    const nbPanel = notebooks?.currentWidget;
    const session = nbPanel?.sessionContext;
    if (!(session?.isReady ?? false)) {
      return;
    }
    const state: IpyflowSessionState = (ipyflowState[session.session.id] ??
      {}) as IpyflowSessionState;
    if (!(state.isIpyflowCommConnected ?? false)) {
      return;
    }
    const notebook = nbPanel.content;
    state.selectedCells = notebook.widgets
      .filter((cell) => cell.model.type === 'code' && notebook.isSelected(cell))
      .map((cell) => cell.model.id);
    updateUI(notebook);
  };
  notebooks.selectionChanged.connect(onSelectionChanged);

  const debouncedSave = debounce(() => {
    const notebook = notebooks.currentWidget;
    if ((notebook.model as any).collaborative ?? false) {
      return;
    } else {
      notebook.context.save();
    }
  }, 200);

  comm.onMsg = (msg) => {
    const payload = msg.content.data;
    if (disconnected || !(payload.success ?? true)) {
      return;
    }
    if (payload.type === 'establish') {
      state.isIpyflowCommConnected = true;
      refreshNodeMapping(notebook);
      notebook.activeCellChanged.connect(onActiveCellChange);
      notebook.activeCell.model.stateChanged.connect(onExecution);
      onActiveCellChange(notebook, notebook.activeCell);
      notebook.model.contentChanged.connect(onContentChanged);
      notebook.model.cells.changed.connect(onContentChanged);
      state.requestComputeExecSchedule();
    } else if (payload.type === 'set_exec_mode') {
      state.numAltModeExecutes = 0;
      state.settings.exec_mode = payload.exec_mode as string;
    } else if (payload.type === 'compute_exec_schedule') {
      state.settings = payload.settings as { [key: string]: string };
      const ipyflow_metadata =
        (notebook.model as any).getMetadata?.('ipyflow') ?? ({} as any);
      const parentsFromMetadata = ipyflow_metadata?.cell_parents ?? {};
      const childrenFromMetadata = ipyflow_metadata?.cell_children ?? {};
      state.cellParents = mergeMaps(
        payload.cell_parents as { [id: string]: string[] },
        parentsFromMetadata
      );
      state.cellChildren = mergeMaps(
        payload.cell_children as { [id: string]: string[] },
        childrenFromMetadata
      );
      state.executedCells = new Set(payload.executed_cells as string[]);
      (notebook.model as any).setMetadata?.('ipyflow', {
        cell_parents: state.cellParents,
        cell_children: state.cellChildren,
      });
      debouncedSave();
      state.waitingCells = new Set(payload.waiting_cells as string[]);
      state.readyCells = new Set(payload.ready_cells as string[]);
      if (state.numPendingForcedReactiveCounterBumps === 0) {
        state.forcedReactiveCells = new Set([
          ...state.forcedReactiveCells,
          ...(payload.forced_reactive_cells as string[]),
        ]);
        state.forcedCascadingReactiveCells = new Set([
          ...state.forcedCascadingReactiveCells,
          ...(payload.forced_cascading_reactive_cells as string[]),
        ]);
      } else {
        state.forcedReactiveCells = new Set();
        state.forcedCascadingReactiveCells = new Set();
      }
      state.waiterLinks = payload.waiter_links as { [id: string]: string[] };
      state.readyMakerLinks = payload.ready_maker_links as {
        [id: string]: string[];
      };
      state.staleParents = payload.stale_parents as {
        [id: string]: string[];
      };
      state.staleParentsByExecutedCellByChild =
        payload.stale_parents_by_executed_cell_by_child as {
          [id: string]: { [id2: string]: string[] };
        };
      state.staleParentsByChildByExecutedCell =
        payload.stale_parents_by_child_by_executed_cell as {
          [id: string]: { [id2: string]: string[] };
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
        state.newReadyCells = new Set();
      }
      const flow_order = payload.flow_order;
      const exec_schedule = payload.exec_schedule;
      state.lastExecutionHighlights = payload.highlights as Highlights;
      const lastExecutedCellId = payload.last_executed_cell_id as string;
      state.executedReactiveReadyCells.add(lastExecutedCellId);
      if (deferredCells.length > 0) {
        const cells = deferredCells.splice(0, deferredCells.length);
        if (state.isBatchReactive()) {
          state.executeClosure(cells);
        } else {
          state.executeCells(cells);
        }
        return;
      }
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
          state.executedCells.add(state.cellPendingExecution.model.id);
          state.executeCells([state.cellPendingExecution]);
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
        if (state.numPendingForcedReactiveCounterBumps > 0) {
          state.bumpForcedReactiveCounter();
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
  const ipyflow_metadata =
    (notebook.model as any).getMetadata?.('ipyflow') ?? ({} as any);
  comm.open({
    interface: 'jupyterlab',
    cell_metadata_by_id: state.gatherCellMetadataAndContent(),
    cell_parents: ipyflow_metadata?.cell_parents ?? {},
    cell_children: ipyflow_metadata?.cell_children ?? {},
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
