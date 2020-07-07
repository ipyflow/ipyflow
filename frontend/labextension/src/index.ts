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
        clearCellState(nbPanel.content);
        let commDisconnectHandler = connectToComm(
          session.session.kernel,
          nbPanel.content
        );
        session.kernelChanged.connect(() => {
          clearCellState(nbPanel.content);
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
              clearCellState(nbPanel.content);
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
const staleOutputClass = 'stale-output-cell';
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

const addStaleOutputInteractions = (elem: HTMLElement) => {
  addStaleOutputInteraction(
      getJpInputCollapser(elem), getJpOutputCollapser(elem), 'mouseover', 'add', linkedStaleClass
  );
  addStaleOutputInteraction(
      getJpInputCollapser(elem), getJpOutputCollapser(elem), 'mouseout', 'remove', linkedStaleClass
  );

  addStaleOutputInteraction(
      getJpOutputCollapser(elem), getJpInputCollapser(elem),
      'mouseover', 'add', linkedRefresherClass
  );
  addStaleOutputInteraction(
      getJpOutputCollapser(elem), getJpInputCollapser(elem),
      'mouseout', 'remove', linkedRefresherClass
  );
};

const clearCellState = (notebook: Notebook) => {
  notebook.widgets.forEach((cell, idx) => {
    cell.node.classList.remove(staleClass);
    cell.node.classList.remove(refresherClass);
    cell.node.classList.remove(staleOutputClass);
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
                                  css: string) => {
  const listener = () => {
    for (const linkedId of linkedElems) {
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
    notebook.widgets.forEach((itercell, idx) => {
      content_by_cell_id[itercell.model.id] = itercell.model.value.text;
      if (itercell.model.id === cell.id) {
        itercell.node.classList.remove(staleOutputClass);
        itercell.node.classList.remove(refresherInputClass);
      }
    });
    const payload = {
      type: 'cell_freshness',
      executed_cell_id: cell.id,
      content_by_cell_id: content_by_cell_id
    };
    comm.send(payload);
  };

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
    }
  }
  notebook.stateChanged.connect(onNotebookStateChange);

  comm.onMsg = (msg) => {
    if (disconnected) {
      return;
    }
    if (msg.content.data['type'] === 'establish') {
      notebook.activeCell.model.stateChanged.connect(onExecution);
    } else if (msg.content.data['type'] === 'cell_freshness') {
      clearCellState(notebook);
      const staleInputCells: any = msg.content.data['stale_input_cells'];
      const staleOutputCells: any = msg.content.data['stale_output_cells'];
      const staleLinks: any = msg.content.data['stale_links'];
      const refresherLinks: any = msg.content.data['refresher_links'];
      const cellsById: {[id: string]: HTMLElement} = {};
      notebook.widgets.forEach((cell, idx) => {
        cellsById[cell.model.id] = cell.node;
      });
      for (const [id, elem] of Object.entries(cellsById)) {
        if (staleInputCells.indexOf(id) > -1) {
          elem.classList.add(staleClass);
          elem.classList.add(staleOutputClass);
          elem.classList.remove(refresherInputClass);
        } else if (staleOutputCells.indexOf(id) > -1) {
          elem.classList.add(refresherInputClass);
          elem.classList.add(staleOutputClass);

          addStaleOutputInteractions(elem);
        }

        if (staleLinks.hasOwnProperty(id)) {
          addUnsafeCellInteraction(
              getJpInputCollapser(elem), staleLinks[id], cellsById, getJpInputCollapser,
              'mouseover', 'add', linkedRefresherClass
          );

          addUnsafeCellInteraction(
              getJpOutputCollapser(elem), staleLinks[id], cellsById, getJpInputCollapser,
              'mouseover', 'add', linkedRefresherClass
          );

          addUnsafeCellInteraction(
              getJpInputCollapser(elem), staleLinks[id], cellsById, getJpInputCollapser,
              'mouseout', 'remove', linkedRefresherClass
          );

          addUnsafeCellInteraction(
              getJpOutputCollapser(elem), staleLinks[id], cellsById, getJpInputCollapser,
              'mouseout', 'remove', linkedRefresherClass
          );
        }

        if (refresherLinks.hasOwnProperty(id)) {
          elem.classList.add(refresherClass);
          elem.classList.add(staleOutputClass);
          addUnsafeCellInteraction(
              getJpInputCollapser(elem), refresherLinks[id], cellsById, getJpInputCollapser,
              'mouseover', 'add', linkedStaleClass
          );

          addUnsafeCellInteraction(
              getJpInputCollapser(elem), refresherLinks[id], cellsById, getJpOutputCollapser,
              'mouseover', 'add', linkedStaleClass,
          );

          addUnsafeCellInteraction(
              getJpInputCollapser(elem), refresherLinks[id], cellsById, getJpInputCollapser,
              'mouseout', 'remove', linkedStaleClass
          );

          addUnsafeCellInteraction(
              getJpInputCollapser(elem), refresherLinks[id], cellsById, getJpOutputCollapser,
              'mouseout', 'remove', linkedStaleClass
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
