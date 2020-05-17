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
        clearStaleAndRefreshMarkerState(nbPanel.content);
        let commDisconnectHandler = connectToComm(
          session.session.kernel,
          nbPanel.content
        );
        session.kernelChanged.connect(() => {
          clearStaleAndRefreshMarkerState(nbPanel.content);
          commDisconnectHandler();
          commDisconnectHandler = connectToComm(
            session.session.kernel,
            nbPanel.content
          );
        });
        session.statusChanged.connect((session, status) => {
          if (status === 'restarting' || status === 'autorestarting') {
            session.ready.then(() => {
              clearStaleAndRefreshMarkerState(nbPanel.content);
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
const refresherClass = 'refresher-cell';
const linkedStaleClass = 'linked-stale';
const linkedRefresherClass = 'linked-refresher';

const getJpCollapser = (elem: HTMLElement) => {
  return elem.children.item(1).firstElementChild;
};

const clearStaleAndRefreshMarkerState = (notebook: Notebook) => {
  notebook.widgets.forEach((cell, idx) => {
    // clear any old event listeners
    const oldCollapser = getJpCollapser(cell.node);
    oldCollapser.firstElementChild.classList.remove(linkedStaleClass);
    oldCollapser.firstElementChild.classList.remove(linkedRefresherClass);

    const newCollapser = oldCollapser.cloneNode(true);
    oldCollapser.parentNode.replaceChild(newCollapser, oldCollapser);

    cell.node.classList.remove(staleClass);
    cell.node.classList.remove(refresherClass);
  });
};

const connectToComm = (
  kernel: Kernel.IKernelConnection,
  notebook: Notebook
) => {
  const comm = kernel.createComm('nbsafety');
  const onExecution = (_: any, args: { notebook: Notebook; cell: any }) => {
    if (notebook !== args.notebook) {
      return;
    }
    const payload: any = {};
    notebook.widgets.forEach((cell, idx) => {
      payload[cell.model.id] = cell.model.value.text;
    });
    comm.send({ payload: payload });
  };
  comm.onMsg = (msg) => {
    if (msg.content.data['type'] === 'establish') {
      NotebookActions.executed.connect(onExecution, NotebookActions.executed);
    } else if (msg.content.data['type'] === 'cell_freshness') {
      clearStaleAndRefreshMarkerState(notebook);
      const staleLinks: any = msg.content.data['stale_links'];
      const refresherLinks: any = msg.content.data['refresher_links'];
      const cellsById: {[id: string]: HTMLElement} = {};
      notebook.widgets.forEach((cell, idx) => {
        cellsById[cell.model.id] = cell.node;
      });
      for (const [id, elem] of Object.entries(cellsById)) {
        if (staleLinks.hasOwnProperty(id)) {
          elem.classList.add(staleClass);
          getJpCollapser(elem).addEventListener('mouseover', () => {
            for (const refresherId of staleLinks[id]) {
              getJpCollapser(cellsById[refresherId]).firstElementChild.classList.add(linkedRefresherClass);
            }
          });
          getJpCollapser(elem).addEventListener('mouseout', () => {
            for (const refresherId of staleLinks[id]) {
              getJpCollapser(cellsById[refresherId]).firstElementChild.classList.remove(linkedRefresherClass);
            }
          });
        } else if (refresherLinks.hasOwnProperty(id)) {
          elem.classList.add(refresherClass);
          getJpCollapser(elem).addEventListener('mouseover', () => {
            for (const staleId of refresherLinks[id]) {
              getJpCollapser(cellsById[staleId]).firstElementChild.classList.add(linkedStaleClass);
            }
          });
          getJpCollapser(elem).addEventListener('mouseout', () => {
            for (const staleId of refresherLinks[id]) {
              getJpCollapser(cellsById[staleId]).firstElementChild.classList.remove(linkedStaleClass);
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
