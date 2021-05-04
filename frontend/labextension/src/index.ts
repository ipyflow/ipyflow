import {
  IChangedArgs
} from '@jupyterlab/coreutils/lib/interfaces';

import {
  ILayoutRestorer,
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import {
  ICellModel
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

const cleanup = new Event('cleanup');

const getJpInputCollapser = (elem: HTMLElement) => {
  return elem.children.item(1).firstElementChild;
};

const getJpOutputCollapser = (elem: HTMLElement) => {
  return elem.children.item(2).firstElementChild;
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

const clearCellState = (notebook: Notebook, lastCellExecPositionIdx: any) => {
  notebook.widgets.forEach((cell, idx) => {
    if (idx < lastCellExecPositionIdx) {
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

const addUnsafeCellInteraction = (elem: Element, linkedElems: [string],
                                  cellsById: {[id: string]: HTMLElement},
                                  collapserFun: (elem: HTMLElement) => Element,
                                  evt: "mouseover" | "mouseout",
                                  add_or_remove: "add" | "remove",
                                  staleCells: [string]) => {
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
    const oldActiveCell = nb.model.cells.get(args.oldValue);
    if (oldActiveCell !== null) {
      oldActiveCell.stateChanged.disconnect(onExecution, oldActiveCell.stateChanged);
    }
    const newActiveCell = nb.model.cells.get(args.newValue);
    if (newActiveCell !== null) {
      newActiveCell.stateChanged.connect(onExecution);
      notifyActiveCell(newActiveCell);
    }
  }
  notebook.stateChanged.connect(onNotebookStateChange);

  comm.onMsg = (msg) => {
    if (disconnected) {
      return;
    }
    if (msg.content.data['type'] === 'establish') {
      notebook.activeCell.model.stateChanged.connect(onExecution);
      notifyActiveCell(notebook.activeCell.model);
    } else if (msg.content.data['type'] === 'cell_freshness') {
      const staleCells: any = msg.content.data['stale_cells'];
      const freshCells: any = msg.content.data['fresh_cells'];
      const staleLinks: any = msg.content.data['stale_links'];
      const refresherLinks: any = msg.content.data['refresher_links'];
      const lastCellExecPositionIdx: any = msg.content.data['last_cell_exec_position_idx'];
      const cellsById: {[id: string]: HTMLElement} = {};
      const orderIdxById: {[id: string]: number} = {};
      clearCellState(notebook, lastCellExecPositionIdx);
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

        if (staleLinks.hasOwnProperty(id)) {
          addUnsafeCellInteraction(
              getJpInputCollapser(elem), staleLinks[id], cellsById, getJpInputCollapser,
              'mouseover', 'add', staleCells
          );

          addUnsafeCellInteraction(
              getJpOutputCollapser(elem), staleLinks[id], cellsById, getJpInputCollapser,
              'mouseover', 'add', staleCells
          );

          addUnsafeCellInteraction(
              getJpInputCollapser(elem), staleLinks[id], cellsById, getJpInputCollapser,
              'mouseout', 'remove', staleCells
          );

          addUnsafeCellInteraction(
              getJpOutputCollapser(elem), staleLinks[id], cellsById, getJpInputCollapser,
              'mouseout', 'remove', staleCells
          );
        }

        if (refresherLinks.hasOwnProperty(id)) {
          if (staleCells.indexOf(id) === -1) {
            elem.classList.add(refresherClass);
            elem.classList.add(freshClass);
          }
          addUnsafeCellInteraction(
              getJpInputCollapser(elem), refresherLinks[id], cellsById, getJpInputCollapser,
              'mouseover', 'add', staleCells
          );

          addUnsafeCellInteraction(
              getJpInputCollapser(elem), refresherLinks[id], cellsById, getJpOutputCollapser,
              'mouseover', 'add', staleCells,
          );

          addUnsafeCellInteraction(
              getJpInputCollapser(elem), refresherLinks[id], cellsById, getJpInputCollapser,
              'mouseout', 'remove', staleCells
          );

          addUnsafeCellInteraction(
              getJpInputCollapser(elem), refresherLinks[id], cellsById, getJpOutputCollapser,
              'mouseout', 'remove', staleCells
          );
        }
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
