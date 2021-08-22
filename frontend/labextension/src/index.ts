import {
  IChangedArgs
} from '@jupyterlab/coreutils/lib/interfaces';

import {
  ILayoutRestorer,
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import {
  ICellModel,
  ICodeCellModel
} from '@jupyterlab/cells';

import {
  INotebookTracker,
  Notebook
} from '@jupyterlab/notebook';

import { Kernel } from '@jupyterlab/services';

/**
 * Initialization data for the jupyterlab-nbsafety extension.
 */
const extension: JupyterFrontEndPlugin<void> = {
  id: 'jupyterlab-nbsafety',
  requires: [ILayoutRestorer, INotebookTracker],
  autoStart: true,
  activate: (
    app: JupyterFrontEnd,
    restorer: ILayoutRestorer,
    notebooks: INotebookTracker
  ) => {
    notebooks.widgetAdded.connect((sender, nbPanel) => {
      const session = nbPanel.sessionContext;
      session.ready.then(() => {
        clearCellState(nbPanel.content, -1);
        // let pasteAction = (<any>NotebookActions).pasteCompleted;
        // if (pasteAction !== undefined) {
        //   console.log('found paste action');
        //   pasteAction.connect((notebook: Notebook, cell: Cell) => {
        //     console.log('Just pasted cell:')
        //     console.log(cell);
        //   })
        // }
        let commDisconnectHandler = connectToComm(
          session.session.kernel,
          nbPanel.content
        );
        session.kernelChanged.connect(() => {
          clearCellState(nbPanel.content, -1);
          commDisconnectHandler();
          commDisconnectHandler = connectToComm(
            session.session.kernel,
            nbPanel.content
          );
        });
        let shouldReconnect = false;
        session.statusChanged.connect((session, status) => {
          if (status === 'restarting' || status === 'autorestarting') {
            shouldReconnect = true;
          }

          if ((status === 'idle' || status === 'busy') && shouldReconnect) {
            shouldReconnect = false;
            session.ready.then(() => {
              clearCellState(nbPanel.content, -1);
              commDisconnectHandler();
              commDisconnectHandler = connectToComm(
                  session.session.kernel,
                  nbPanel.content
              );
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

let dirtyCells: string[] = [];
let staleCells: string[] = [];
let freshCells: string[] = [];
let staleLinks: {[id: string]: string[]} = {}
let refresherLinks: {[id: string]: string[]} = {}
let refresherCells: string[] = [];
let lastCellExecPositionIdx: number = -1;

const cleanup = new Event('cleanup');

const getJpInputCollapser = (elem: HTMLElement) => {
  const child = elem.children.item(1);
  if (child === null) {
    return null;
  }
  return child.firstElementChild;
};

const getJpOutputCollapser = (elem: HTMLElement) => {
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

const clearCellState = (notebook: Notebook, execIdx?: number) => {
  if (execIdx === null) {
    execIdx = lastCellExecPositionIdx;
  }
  notebook.widgets.forEach((cell, idx) => {
    if (idx < execIdx) {
      return;
    }
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
                                  staleCells: string[]) => {
  const listener = () => {
    for (const linkedId of linkedElems) {
      let css = linkedRefresherClass;
      if (staleCells.indexOf(linkedId) > -1) {
        css = linkedStaleClass;
      }
      collapserFun(cellsById[linkedId]).firstElementChild.classList[add_or_remove](css);
    }
  };
  elem.addEventListener(evt, listener);
  attachCleanupListener(elem, evt, listener);
};

const connectToComm = (
  kernel: Kernel.IKernelConnection,
  notebook: Notebook
) => {
  const comm = kernel.createComm('nbsafety');
  let disconnected = false;

  const onExecution: any = (cell: ICellModel, args: IChangedArgs<any>) => {
    if (disconnected) {
      cell.stateChanged.disconnect(onExecution);
      return;
    }
    if (args.name !== 'executionCount' || args.newValue === null) {
      return;
    }
    const content_by_cell_id: {[id: string]: string} = {};
    const order_index_by_cell_id: {[id: string]: number} = {};
    notebook.widgets.forEach((itercell, idx) => {
      content_by_cell_id[itercell.model.id] = itercell.model.value.text;
      order_index_by_cell_id[itercell.model.id] = idx;
      if (itercell.model.id === cell.id) {
        itercell.node.classList.remove(freshClass);
        itercell.node.classList.remove(refresherInputClass);
      }
    });
    const payload = {
      type: 'cell_freshness',
      executed_cell_id: cell.id,
      content_by_cell_id: content_by_cell_id,
      order_index_by_cell_id: order_index_by_cell_id,
    };
    comm.send(payload);
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
  }

  const onNotebookStateChange = (nb: Notebook, args: IChangedArgs<any>) => {
    if (disconnected) {
      nb.stateChanged.disconnect(onNotebookStateChange);
      return;
    }
    if (args.name !== 'activeCellIndex' || nb !== notebook) {
      return;
    }
    // console.log('state changed');
    // console.log(`dirty cells: ${dirtyCells}`)
    const oldActiveCell = nb.model.cells.get(args.oldValue);
    if (oldActiveCell !== null) {
      oldActiveCell.stateChanged.disconnect(onExecution, oldActiveCell.stateChanged);
      if ((<ICodeCellModel>oldActiveCell).isDirty) {
        dirtyCells.push(oldActiveCell.id);
      }
    }
    const newActiveCell = nb.model.cells.get(args.newValue);
    if (newActiveCell === null) {
      return;
    }
    newActiveCell.stateChanged.connect(onExecution);
    notifyActiveCell(newActiveCell);
    if (newActiveCell.type === "code") {
      dirtyCells.forEach((cell_id, _) => {
        if (cell_id === newActiveCell.id) {
          // console.log(`found one: ${newActiveCell.id}`);
          (<any>newActiveCell)._setDirty(true);
        }
      });
    }
  }
  notebook.stateChanged.connect(onNotebookStateChange);

  const updateUI = (notebook: Notebook) => {
    clearCellState(notebook);
    const cellsById: {[id: string]: HTMLElement} = {};
    const orderIdxById: {[id: string]: number} = {};

    notebook.widgets.forEach((cell, idx) => {
      cellsById[cell.model.id] = cell.node;
      orderIdxById[cell.model.id] = idx;
    });
    for (const [id, elem] of Object.entries(cellsById)) {
      if (orderIdxById[id] < lastCellExecPositionIdx) {
        continue;
      }
      if (staleCells.indexOf(id) > -1) {
        elem.classList.add(staleClass);
        elem.classList.add(freshClass);
        elem.classList.remove(refresherInputClass);
        addStaleOutputInteractions(elem, linkedStaleClass);
      } else if (freshCells.indexOf(id) > -1) {
        elem.classList.add(refresherInputClass);
        elem.classList.add(freshClass);
        addStaleOutputInteractions(elem, linkedRefresherClass);
      }

      const actionUpdatePairs: {action: "mouseover" | "mouseout"; update: "add" | "remove"}[] = [
        {
          action: 'mouseover',
          update: 'add',
        }, {
          action: 'mouseout',
          update: 'remove',
        }
      ];

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
        refresherCells.push(id);
        if (staleCells.indexOf(id) === -1) {
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
    }
  }

  comm.onMsg = (msg) => {
    if (disconnected) {
      return;
    }
    if (msg.content.data['type'] === 'establish') {
      notebook.activeCell.model.stateChanged.connect(onExecution);
      notifyActiveCell(notebook.activeCell.model);
    } else if (msg.content.data['type'] === 'cell_freshness') {
      staleCells = msg.content.data['stale_cells'] as string[];
      freshCells = msg.content.data['fresh_cells'] as string[];
      staleLinks = msg.content.data['stale_links'] as { [id: string]: string[] };
      refresherLinks = msg.content.data['refresher_links'] as { [id: string]: string[] };
      lastCellExecPositionIdx = msg.content.data['last_cell_exec_position_idx'] as number;
      updateUI(notebook);
    }
  };
  comm.open({});
  // return a disconnection handle
  return () => {
    disconnected = true;
  };
};

export default extension;
