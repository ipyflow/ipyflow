import {
  IChangedArgs
} from '@jupyterlab/coreutils/lib/interfaces';

import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import {
  ISessionContext
} from '@jupyterlab/apputils';

import {
  Cell,
  CodeCell,
  ICellModel,
  ICodeCellModel
} from '@jupyterlab/cells';

import {
  INotebookTracker,
  Notebook
} from '@jupyterlab/notebook';

const NBSAFETY_KERNEL_NAME: string = 'nbsafety';

/**
 * Initialization data for the jupyterlab-nbsafety extension.
 */
const extension: JupyterFrontEndPlugin<void> = {
  id: 'jupyterlab-nbsafety',
  requires: [INotebookTracker],
  autoStart: true,
  activate: (
    app: JupyterFrontEnd,
    notebooks: INotebookTracker
  ) => {
    notebooks.widgetAdded.connect((sender, nbPanel) => {
      const session = nbPanel.sessionContext;
      session.ready.then(() => {
        clearCellState(nbPanel.content);
        activeCell = nbPanel.content.activeCell;
        activeCellId = nbPanel.content.activeCell.model.id;
        let commDisconnectHandler = () => {};
        if (session.session.kernel.name === NBSAFETY_KERNEL_NAME) {
          commDisconnectHandler = connectToComm(
            session,
            nbPanel.content
          );
        }
        session.kernelChanged.connect((_, args) => {
          clearCellState(nbPanel.content);
          commDisconnectHandler();
          commDisconnectHandler = () => {};
          if (args.newValue !== null && args.newValue.name === NBSAFETY_KERNEL_NAME) {
            commDisconnectHandler = connectToComm(
              session,
              nbPanel.content
            );
          }
        });
        let shouldReconnect = false;
        session.statusChanged.connect((session, status) => {
          if (status === 'restarting' || status === 'autorestarting') {
            shouldReconnect = true;
          }

          if ((status === 'idle' || status === 'busy') && shouldReconnect) {
            shouldReconnect = false;
            session.ready.then(() => {
              clearCellState(nbPanel.content);
              commDisconnectHandler();
              commDisconnectHandler = () => {};
              if (session.session.kernel.name === NBSAFETY_KERNEL_NAME) {
                commDisconnectHandler = connectToComm(
                    session,
                    nbPanel.content
                );
              }
            });
          }
        });
      });
    });
  }
};

const staleClass = 'stale-cell';
const freshClass = 'fresh-cell';
const refresherClass = 'refresher-cell';
const refresherInputClass = 'refresher-input-cell';
const linkedStaleClass = 'linked-stale';
const linkedRefresherClass = 'linked-refresher';

let dirtyCells: Set<string> = new Set();
let staleCells: Set<string> = new Set();
let freshCells: Set<string> = new Set();
let staleLinks: {[id: string]: string[]} = {}
let refresherLinks: {[id: string]: string[]} = {}
let activeCell: Cell<ICellModel> = null;
let activeCellId: string = null;
let cellsById: {[id: string]: HTMLElement} = {};
let orderIdxById: {[id: string]: number} = {};
let cellPendingExecution: CodeCell = null;

let lastExecutionMode: string = null;
let lastExecutionHighlightsEnabled: boolean = null;
let executedReactiveFreshCells: Set<string> = new Set();
let newFreshCells: Set<string> = new Set();

const cleanup = new Event('cleanup');

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

const attachCleanupListener = (elem: Element, evt: "mouseover" | "mouseout", listener: any) => {
  const cleanupListener = () => {
    elem.removeEventListener(evt, listener);
    elem.removeEventListener('cleanup', cleanupListener);
  };
  elem.addEventListener(evt, listener);
  elem.addEventListener('cleanup', cleanupListener);
};

const addStaleOutputInteraction = (elem: Element,
                                   linkedElem: Element,
                                   evt: "mouseover" | "mouseout",
                                   add_or_remove: "add" | "remove",
                                   css: string) => {
  if (elem === null || linkedElem === null) {
    return;
  }
  const listener = () => {
    linkedElem.firstElementChild.classList[add_or_remove](css);
  };
  attachCleanupListener(elem, evt, listener);
};

const addStaleOutputInteractions = (elem: HTMLElement, linkedInputClass: string) => {
  addStaleOutputInteraction(
      getJpInputCollapser(elem), getJpOutputCollapser(elem), 'mouseover', 'add', linkedStaleClass
  );
  addStaleOutputInteraction(
      getJpInputCollapser(elem), getJpOutputCollapser(elem), 'mouseout', 'remove', linkedStaleClass
  );

  addStaleOutputInteraction(
      getJpOutputCollapser(elem), getJpInputCollapser(elem),
      'mouseover', 'add', linkedInputClass
  );
  addStaleOutputInteraction(
      getJpOutputCollapser(elem), getJpInputCollapser(elem),
      'mouseout', 'remove', linkedInputClass
  );
};


const refreshNodeMapping = (notebook: Notebook) => {
  cellsById = {};
  orderIdxById = {};

  notebook.widgets.forEach((cell, idx) => {
    cellsById[cell.model.id] = cell.node;
    orderIdxById[cell.model.id] = idx;
  });
}

const clearCellState = (notebook: Notebook) => {
  notebook.widgets.forEach((cell, idx) => {
    cell.node.classList.remove(staleClass);
    cell.node.classList.remove(refresherClass);
    cell.node.classList.remove(freshClass);
    cell.node.classList.remove(refresherInputClass);

    // clear any old event listeners
    const inputCollapser = getJpInputCollapser(cell.node);
    if (inputCollapser !== null) {
      inputCollapser.firstElementChild.classList.remove(linkedStaleClass);
      inputCollapser.firstElementChild.classList.remove(linkedRefresherClass);
      inputCollapser.dispatchEvent(cleanup);
    }

    const outputCollapser = getJpOutputCollapser(cell.node);
    if (outputCollapser !== null) {
      outputCollapser.firstElementChild.classList.remove(linkedStaleClass);
      outputCollapser.firstElementChild.classList.remove(linkedRefresherClass);
      outputCollapser.dispatchEvent(cleanup);
    }
  });
};

const addUnsafeCellInteraction = (elem: Element, linkedElems: string[],
                                  cellsById: {[id: string]: HTMLElement},
                                  collapserFun: (elem: HTMLElement) => Element,
                                  evt: "mouseover" | "mouseout",
                                  add_or_remove: "add" | "remove",
                                  staleCells: Set<string>) => {
  if (elem === null) {
    return;
  }
  const listener = () => {
    for (const linkedId of linkedElems) {
      let css = linkedRefresherClass;
      if (staleCells.has(linkedId)) {
        css = linkedStaleClass;
      }
      const collapser = collapserFun(cellsById[linkedId]);
      if (collapser === null || collapser.firstElementChild === null) {
        return;
      }
      collapser.firstElementChild.classList[add_or_remove](css);
    }
  };
  elem.addEventListener(evt, listener);
  attachCleanupListener(elem, evt, listener);
};

const connectToComm = (
  session: ISessionContext,
  notebook: Notebook
) => {
  const comm = session.session.kernel.createComm('nbsafety');
  let disconnected = false;

  const onExecution = (cell: ICellModel, args: IChangedArgs<any>) => {
    if (disconnected) {
      cell.stateChanged.disconnect(onExecution);
      return;
    }
    if (args.name !== 'executionCount' || args.newValue === null) {
      return;
    }
    const order_index_by_cell_id: {[id: string]: number} = {};
    notebook.widgets.forEach((itercell, idx) => {
      order_index_by_cell_id[itercell.model.id] = idx;
      if (itercell.model.id === cell.id) {
        itercell.node.classList.remove(freshClass);
        itercell.node.classList.remove(refresherInputClass);
      }
    });
    const payload = {
      type: 'cell_freshness',
      executed_cell_id: cell.id,
      order_index_by_cell_id: order_index_by_cell_id,
    };
    comm.send(payload).done.then(() => {
      if (cellPendingExecution !== null) {
        CodeCell.execute(cellPendingExecution, session)
      } else if (lastExecutionMode === 'reactive') {
        freshCells = executedReactiveFreshCells;
        newFreshCells = new Set<string>();
        executedReactiveFreshCells = new Set<string>();
        updateUI(notebook);
      }
    });
  };


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
      active_cell_order_idx: newActiveCellOrderIdx
    }
    comm.send(payload);
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
    if (activeCell !== null && activeCell.model !== null) {
      if ((<ICodeCellModel>activeCell.model).isDirty) {
        dirtyCells.add(activeCellId);
      } else {
        dirtyCells.delete(activeCellId);
      }
      activeCell.model.stateChanged.disconnect(onExecution, activeCell.model.stateChanged);
    }
    activeCell = cell;
    activeCellId = cell.model.id;

    if (activeCell === null || activeCell.model === null || activeCell.model.type !== "code") {
      return;
    }

    activeCell.model.stateChanged.connect(onExecution);
    notifyActiveCell(activeCell.model);

    if (dirtyCells.has(activeCellId)) {
      const activeCellModel: any = activeCell.model as any;
      if (activeCellModel._setDirty !== undefined) {
        activeCellModel._setDirty(true);
      }
    }
    refreshNodeMapping(notebook);
    updateOneCellUI(activeCellId);
  };
  notebook.activeCellChanged.connect(onActiveCellChange);

  const actionUpdatePairs: {action: "mouseover" | "mouseout"; update: "add" | "remove"}[] = [
    {
      action: 'mouseover',
      update: 'add',
    }, {
      action: 'mouseout',
      update: 'remove',
    }
  ];

  const updateOneCellUI = (id: string) => {
    const elem = cellsById[id];
    if (staleCells.has(id)) {
      elem.classList.add(staleClass);
      elem.classList.add(freshClass);
      elem.classList.remove(refresherInputClass);
      addStaleOutputInteractions(elem, linkedStaleClass);
    } else if (freshCells.has(id)) {
      elem.classList.add(refresherInputClass);
      if (lastExecutionMode === 'normal') {
        elem.classList.add(freshClass);
        addStaleOutputInteractions(elem, linkedRefresherClass);
      }
    }

    if (lastExecutionMode === 'reactive') {
      return;
    }

    if (staleLinks.hasOwnProperty(id)) {
      actionUpdatePairs.forEach(({action, update}) => {
        addUnsafeCellInteraction(
            getJpInputCollapser(elem), staleLinks[id], cellsById, getJpInputCollapser,
            action, update, staleCells
        );

        addUnsafeCellInteraction(
            getJpOutputCollapser(elem), staleLinks[id], cellsById, getJpInputCollapser,
            action, update, staleCells,
        );
      });
    }

    if (refresherLinks.hasOwnProperty(id)) {
      if (!staleCells.has(id)) {
        elem.classList.add(refresherClass);
        elem.classList.add(freshClass);
      }
      actionUpdatePairs.forEach(({action, update}) => {
        addUnsafeCellInteraction(
            getJpInputCollapser(elem), refresherLinks[id], cellsById, getJpInputCollapser,
            action, update, staleCells
        );

        addUnsafeCellInteraction(
            getJpInputCollapser(elem), refresherLinks[id], cellsById, getJpOutputCollapser,
            action, update, staleCells,
        );
      });
    }
  };

  const updateUI = (notebook: Notebook) => {
    clearCellState(notebook);
    if (!lastExecutionHighlightsEnabled) {
      return;
    }
    refreshNodeMapping(notebook);
    for (const [id] of Object.entries(cellsById)) {
      updateOneCellUI(id);
    }
  };

  comm.onMsg = (msg) => {
    if (disconnected) {
      return;
    }
    if (msg.content.data['type'] === 'establish') {
      notebook.activeCell.model.stateChanged.connect(onExecution);
      notifyActiveCell(notebook.activeCell.model);
    } else if (msg.content.data['type'] === 'cell_freshness') {
      staleCells = new Set(msg.content.data['stale_cells'] as string[]);
      freshCells = new Set(msg.content.data['fresh_cells'] as string[]);
      newFreshCells = new Set([...newFreshCells, ...msg.content.data['new_fresh_cells'] as string[]])
      staleLinks = msg.content.data['stale_links'] as { [id: string]: string[] };
      refresherLinks = msg.content.data['refresher_links'] as { [id: string]: string[] };
      cellPendingExecution = null;
      const exec_mode = msg.content.data['exec_mode'] as string;
      lastExecutionMode = exec_mode;
      lastExecutionHighlightsEnabled = msg.content.data['highlights_enabled'] as boolean;
      if (exec_mode === 'normal') {
        newFreshCells = new Set<string>();
        updateUI(notebook);
      } else if (exec_mode === 'reactive') {
        executedReactiveFreshCells.add(msg.content.data['last_executed_cell_id'] as string);
        clearCellState(notebook);
        let found = false;
        notebook.widgets.forEach(cell => {
          if (found) {
            return;
          }
          if (newFreshCells.has(cell.model.id) && !executedReactiveFreshCells.has(cell.model.id)) {
            if (cell.model.type === 'code') {
              cellPendingExecution = (cell as CodeCell);
            }
            found = true;
          }
        });
      } else {
        console.warn(`Unknown execution mode: ${exec_mode}`)
      }
    }
  };
  comm.open({});
  // return a disconnection handle
  return () => {
    disconnected = true;
  };
};

export default extension;
