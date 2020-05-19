import {
  ILayoutRestorer,
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import { Kernel } from '@jupyterlab/services';

import {
  INotebookTracker,
  Notebook,
  NotebookActions
} from '@jupyterlab/notebook';

import {
  Cell
} from '@jupyterlab/cells';

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
        session.statusChanged.connect((session, status) => {
          if (status === 'restarting' || status === 'autorestarting') {
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

const getJpInputCollapser = (elem: HTMLElement) => {
  return elem.children.item(1).firstElementChild;
};

const getJpOutputCollapser = (elem: HTMLElement) => {
  return elem.children.item(2).firstElementChild;
};

const clearCellState = (notebook: Notebook) => {
  notebook.widgets.forEach((cell, idx) => {
    // clear any old event listeners
    const oldInputCollapser = getJpInputCollapser(cell.node);
    oldInputCollapser.firstElementChild.classList.remove(linkedStaleClass);
    oldInputCollapser.firstElementChild.classList.remove(linkedRefresherClass);

    const newInputCollapser = oldInputCollapser.cloneNode(true);
    oldInputCollapser.parentNode.replaceChild(newInputCollapser, oldInputCollapser);

    const oldOutputCollapser = getJpOutputCollapser(cell.node);
    oldOutputCollapser.firstElementChild.classList.remove(linkedStaleClass);
    oldOutputCollapser.firstElementChild.classList.remove(linkedRefresherClass);

    const newOutputCollapser = oldOutputCollapser.cloneNode(true);
    oldOutputCollapser.parentNode.replaceChild(newOutputCollapser, oldOutputCollapser);

    cell.node.classList.remove(staleClass);
    cell.node.classList.remove(refresherClass);

    cell.node.classList.remove(staleOutputClass);
    cell.node.classList.remove(refresherInputClass);
  });
};

const connectToComm = (
  kernel: Kernel.IKernelConnection,
  notebook: Notebook
) => {
  const comm = kernel.createComm('nbsafety');
  const onExecution = (_: any, args: { notebook: Notebook; cell: Cell }) => {
    if (notebook !== args.notebook) {
      return;
    }
    const content_by_cell_id: any = {};
    args.cell.node.classList.remove(staleOutputClass);
    args.cell.node.classList.remove(refresherInputClass);
    notebook.widgets.forEach((cell, idx) => {
      content_by_cell_id[cell.model.id] = cell.model.value.text;
    });
    comm.send({
      executed_cell_id: args.cell.model.id,
      content_by_cell_id: content_by_cell_id
    });
  };
  comm.onMsg = (msg) => {
    if (msg.content.data['type'] === 'establish') {
      NotebookActions.executed.connect(onExecution, NotebookActions.executed);
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

          getJpInputCollapser(elem).addEventListener('mouseover', () => {
              getJpOutputCollapser(elem).firstElementChild.classList.add(linkedStaleClass);
          });
          getJpInputCollapser(elem).addEventListener('mouseout', () => {
            getJpOutputCollapser(elem).firstElementChild.classList.remove(linkedStaleClass);
          });

          getJpOutputCollapser(elem).addEventListener('mouseover', () => {
            getJpInputCollapser(elem).firstElementChild.classList.add(linkedRefresherClass);
          });
          getJpOutputCollapser(elem).addEventListener('mouseout', () => {
            getJpInputCollapser(elem).firstElementChild.classList.remove(linkedRefresherClass);
          });
        }

        if (staleLinks.hasOwnProperty(id)) {
          getJpInputCollapser(elem).addEventListener('mouseover', () => {
            for (const refresherId of staleLinks[id]) {
              getJpInputCollapser(cellsById[refresherId]).firstElementChild.classList.add(linkedRefresherClass);
            }
          });
          getJpOutputCollapser(elem).addEventListener('mouseover', () => {
            for (const refresherId of staleLinks[id]) {
              getJpInputCollapser(cellsById[refresherId]).firstElementChild.classList.add(linkedRefresherClass);
            }
          });
          getJpInputCollapser(elem).addEventListener('mouseout', () => {
            for (const refresherId of staleLinks[id]) {
              getJpInputCollapser(cellsById[refresherId]).firstElementChild.classList.remove(linkedRefresherClass);
            }
          });
          getJpOutputCollapser(elem).addEventListener('mouseout', () => {
            for (const refresherId of staleLinks[id]) {
              getJpInputCollapser(cellsById[refresherId]).firstElementChild.classList.remove(linkedRefresherClass);
            }
          });
        }

        if (refresherLinks.hasOwnProperty(id)) {
          elem.classList.add(refresherClass);
          elem.classList.add(staleOutputClass);
          getJpInputCollapser(elem).addEventListener('mouseover', () => {
            for (const staleId of refresherLinks[id]) {
              getJpInputCollapser(cellsById[staleId]).firstElementChild.classList.add(linkedStaleClass);
            }
          });
          getJpInputCollapser(elem).addEventListener('mouseover', () => {
            for (const staleId of refresherLinks[id]) {
              getJpOutputCollapser(cellsById[staleId]).firstElementChild.classList.add(linkedStaleClass);
            }
          });
          getJpInputCollapser(elem).addEventListener('mouseout', () => {
            for (const staleId of refresherLinks[id]) {
              getJpInputCollapser(cellsById[staleId]).firstElementChild.classList.remove(linkedStaleClass);
            }
          });
          getJpInputCollapser(elem).addEventListener('mouseout', () => {
            for (const staleId of refresherLinks[id]) {
              getJpOutputCollapser(cellsById[staleId]).firstElementChild.classList.remove(linkedStaleClass);
            }
          });
        }
      }
    }
  };
  comm.open({});
  // return a disconnection handle
  return () => {
    NotebookActions.executed.disconnect(onExecution, NotebookActions.executed);
  };
};

export default extension;
