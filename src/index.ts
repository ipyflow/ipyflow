import {
  ILayoutRestorer,
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

import { KernelMessage, Kernel } from '@jupyterlab/services';

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
        let commDisconnectHandler = connectToComm(
          session.session.kernel,
          nbPanel.content
        );
        session.statusChanged.connect((session, status) => {
          if (status === 'restarting' || status === 'autorestarting') {
            session.ready.then(() => {
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

const connectToComm = (
  kernel: Kernel.IKernelConnection,
  notebook: Notebook
) => {
  const comm = kernel.createComm('nbsafety');
  comm.open({});
  comm.onMsg = (msg: KernelMessage.ICommMsgMsg) => {
    const staleCellIds: any = msg['content']['data']['stale_cells'];
    const refresherCellIds: any = msg['content']['data']['refresher_cells'];
    notebook.widgets.forEach((cell, idx) => {
      const inputCollapser =
        cell.node.childNodes[1].firstChild.parentElement.firstElementChild;
      if (staleCellIds.indexOf(cell.model.id) > -1) {
        inputCollapser.classList.add(staleClass);
      } else if (refresherCellIds.indexOf(cell.model.id) > -1) {
        inputCollapser.classList.add(refresherClass);
      } else {
        inputCollapser.classList.remove(staleClass);
        inputCollapser.classList.remove(refresherClass);
      }
    });
  };
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
  NotebookActions.executed.connect(onExecution, NotebookActions.executed);
  // return a disconnection handle
  return () => {
    NotebookActions.executed.disconnect(onExecution, NotebookActions.executed);
  };
};

export default extension;
