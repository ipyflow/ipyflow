import '../style/index.css';
// import "jqueryui";

type Highlights = 'all' | 'none' | 'executed' | 'reactive';

const waitingClass = 'waiting-cell';
const readyClass = 'ready-cell';
const readyMakingClass = 'ready-making-cell';
const readyMakingInputClass = 'ready-making-input-cell';
const linkedWaitingClass = 'linked-waiting';
const linkedReadyClass = 'linked-ready';
const linkedReadyMakingClass = 'linked-ready-making';

const cleanup = new Event('cleanup');

// ipyflow frontend state
let isIpyflowCommConnected = false;
let waitingCells: Set<string> = new Set();
let readyCells: Set<string> = new Set();
let waiterLinks: { [id: string]: string[] } = {};
let readyMakerLinks: { [id: string]: string[] } = {};
let activeCell: any | null = null;
let activeCellIdx: number | null = null;
let activeCellToReturnToAfterReactiveExecution: any | null = null;
let cellsById: { [id: string]: HTMLElement } = {};
let cellModelsById: { [id: string]: any } = {};
let orderIdxById: { [id: string]: number } = {};
let cellPendingExecution: any | null = null;
let cellPendingExecutionIdx: number | null = null;

let lastExecutionMode: string | null = null;
let isReactivelyExecuting = false;
let isAltModeExecuting = false;
let lastExecutionHighlights: Highlights | null = null;
let executedReactiveReadyCells: Set<string> = new Set();
let newReadyCells: Set<string> = new Set();
let forcedReactiveCells: Set<string> = new Set();

function getCellInputSection(elem: HTMLElement): Element | null {
  if (elem === null) {
    return null;
  }
  if (elem.firstElementChild === null) {
    return null;
  }
  if (elem.firstElementChild.firstElementChild === null) {
    return null;
  }
  return elem.firstElementChild.firstElementChild.firstElementChild;
}

function getCellOutputSection(elem: HTMLElement): Element | null {
  if (elem === null) {
    return null;
  }
  if (elem.children.item(1) === null) {
    return null;
  }
  return elem.children.item(1).firstElementChild;
}

const attachCleanupListener = (
  elem: any,
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

function addReadyInteraction(
  elem: Element | null,
  linkedElem: Element,
  evt: 'mouseover' | 'mouseout',
  add_or_remove: 'add' | 'remove',
  css: string
): void {
  if (elem === null) {
    return;
  }
  const listener = () => {
    linkedElem.classList[add_or_remove](css);
  };
  attachCleanupListener(elem, evt, listener);
}

function addReadyInteractions(elem: HTMLElement): void {
  addReadyInteraction(
    getCellInputSection(elem),
    elem,
    'mouseover',
    'add',
    linkedReadyClass
  );
  addReadyInteraction(
    getCellInputSection(elem),
    elem,
    'mouseout',
    'remove',
    linkedReadyClass
  );

  addReadyInteraction(
    getCellOutputSection(elem),
    elem,
    'mouseover',
    'add',
    linkedReadyMakingClass
  );
  addReadyInteraction(
    getCellOutputSection(elem),
    elem,
    'mouseout',
    'remove',
    linkedReadyMakingClass
  );
}

function addUnsafeCellInteraction(
  elem: Element,
  linkedElems: string[],
  cellsById: { [id: string]: HTMLElement },
  evt: 'mouseover' | 'mouseout',
  add_or_remove: 'add' | 'remove',
  css: string
): void {
  const listener = () => {
    for (const linkedId of linkedElems) {
      cellsById[linkedId].classList[add_or_remove](css);
    }
  };
  elem.addEventListener(evt, listener);
  attachCleanupListener(elem, evt, listener);
}

function addUnsafeCellInteractions(
  elem: HTMLElement,
  linkedElems: string[],
  cellsById: { [id: string]: HTMLElement }
): void {
  addUnsafeCellInteraction(
    getCellInputSection(elem),
    linkedElems,
    cellsById,
    'mouseover',
    'add',
    linkedReadyMakingClass
  );

  addUnsafeCellInteraction(
    getCellOutputSection(elem),
    linkedElems,
    cellsById,
    'mouseover',
    'add',
    linkedReadyMakingClass
  );

  addUnsafeCellInteraction(
    getCellInputSection(elem),
    linkedElems,
    cellsById,
    'mouseout',
    'remove',
    linkedReadyMakingClass
  );

  addUnsafeCellInteraction(
    getCellOutputSection(elem),
    linkedElems,
    cellsById,
    'mouseout',
    'remove',
    linkedReadyMakingClass
  );
}

function addReadyMakerCellInteractions(
  elem: HTMLElement,
  linkedElems: string[],
  cellsById: { [id: string]: HTMLElement }
): void {
  addUnsafeCellInteraction(
    getCellInputSection(elem),
    linkedElems,
    cellsById,
    'mouseover',
    'add',
    linkedWaitingClass
  );

  addUnsafeCellInteraction(
    getCellInputSection(elem),
    linkedElems,
    cellsById,
    'mouseover',
    'add',
    linkedReadyClass
  );

  addUnsafeCellInteraction(
    getCellInputSection(elem),
    linkedElems,
    cellsById,
    'mouseout',
    'remove',
    linkedWaitingClass
  );

  addUnsafeCellInteraction(
    getCellInputSection(elem),
    linkedElems,
    cellsById,
    'mouseout',
    'remove',
    linkedReadyClass
  );
}

function refreshNodeMapping(Jupyter: any): void {
  cellsById = {};
  cellModelsById = {};
  orderIdxById = {};

  Jupyter.notebook.get_cells().forEach((cell: any, idx: number) => {
    cellsById[cell.id] = cell.element[0];
    cellModelsById[cell.id] = cell;
    orderIdxById[cell.id] = idx;
  });
}

function clearOneCellState(cell: any): void {
  const elem = cell.element[0];
  elem.classList.remove(waitingClass);
  elem.classList.remove(readyMakingClass);
  elem.classList.remove(readyClass);
  elem.classList.remove(readyMakingInputClass);
  elem.classList.remove(linkedWaitingClass);
  elem.classList.remove(linkedReadyClass);
  elem.classList.remove(linkedReadyMakingClass);

  const cellInput = getCellInputSection(elem);
  if (cellInput !== null) {
    cellInput.dispatchEvent(cleanup);
  }

  const cellOutput = getCellOutputSection(elem);
  if (cellOutput !== null) {
    cellOutput.dispatchEvent(cleanup);
  }
}

function clearCellState(Jupyter: any): void {
  Jupyter.notebook.get_cells().forEach((cell: any) => {
    clearOneCellState(cell);
  });
}

function updateOneCellUI(cell: any): void {
  clearOneCellState(cell);
  const id = cell.id;
  const elem = cell.element[0];
  if (waitingCells.has(id)) {
    elem.classList.add(waitingClass);
    elem.classList.add(readyClass);
    elem.classList.remove(readyMakingInputClass);
  } else if (readyCells.has(id)) {
    elem.classList.add(readyMakingInputClass);
    elem.classList.add(readyClass);
    addReadyInteractions(elem);
  }

  if (lastExecutionMode === 'reactive') {
    return;
  }

  if (Object.prototype.hasOwnProperty.call(waiterLinks, id)) {
    addUnsafeCellInteractions(elem, waiterLinks[id], cellsById);
  }

  if (Object.prototype.hasOwnProperty.call(readyMakerLinks, id)) {
    elem.classList.add(readyMakingClass);
    elem.classList.add(readyClass);
    addReadyInteractions(elem);
    addReadyMakerCellInteractions(elem, readyMakerLinks[id], cellsById);
  }
}

function updateUI(Jupyter: any): void {
  if (lastExecutionHighlights === 'none') {
    return;
  }
  refreshNodeMapping(Jupyter);
  Jupyter.notebook.get_cells().forEach((cell: any) => {
    updateOneCellUI(cell);
  });
}

type CellMetadataMap = {
  [id: string]: {
    index: number;
    content: string;
    type: string;
  };
};

function gatherCellMetadataById(Jupyter: any): CellMetadataMap {
  const cell_metadata_by_id: CellMetadataMap = {};
  Jupyter.notebook.get_cells().forEach((cell: any, idx: number) => {
    if (cell.cell_type !== 'code') {
      return;
    }
    cell_metadata_by_id[cell.id] = {
      index: idx,
      content: cell.get_text(),
      type: cell.cell_type
    };
  });
  return cell_metadata_by_id;
}

function connectToComm(Jupyter: any, code_cell: any): () => void {
  let disconnected = false;
  const comm = Jupyter.notebook.kernel.comm_manager.new_comm('ipyflow', {
    interface: 'jupyter'
  });

  const keybinding = {
    help: 'alt mode execute',
    help_index: 'zz',
    handler: () => {
      if (activeCell?.cell_type !== 'code') {
        return;
      }
      notifyActiveCell();
      if (isAltModeExecuting) {
        // ensure that we keep exec_mode the same
        // when next toggle runs
        Jupyter.notebook.kernel.execute(
          '%flow toggle-reactivity-until-next-reset',
          {
            silent: true,
            store_history: false
          }
        );
      }
      isAltModeExecuting = true;
      Jupyter.notebook.kernel.execute(
        '%flow toggle-reactivity-until-next-reset',
        {
          silent: true,
          store_history: false
        }
      );
      Jupyter.notebook.execute_cells([activeCellIdx]);
    }
  };

  const onExecution = (evt: any, data: { cell: any }) => {
    if (disconnected) {
      Jupyter.notebook.events.unbind('execute.CodeCell', onExecution);
      return;
    }
    if (data.cell.notebook !== Jupyter.notebook) {
      return;
    }
    data.cell.element[0].classList.remove(readyClass);
    data.cell.element[0].classList.remove(readyMakingInputClass);
    comm.send({
      type: 'compute_exec_schedule',
      executed_cell_id: data.cell.id,
      cell_metadata_by_id: gatherCellMetadataById(Jupyter)
    });
  };

  const onSelect = (evt: any, data: { cell: any }) => {
    if (disconnected) {
      Jupyter.notebook.events.unbind('select.Cell', onSelect);
      return;
    }
    if (data.cell == null) {
      return;
    }
    Jupyter.notebook.get_cells().forEach((cell: any, idx: number) => {
      if (data.cell.id === cell.id) {
        activeCell = data.cell;
        activeCellIdx = idx;
      }
    });
    comm.send({
      type: 'change_active_cell',
      active_cell_id: data.cell.id,
      active_cell_order_idx: activeCellIdx
    });
  };

  // TODO: need a way to ensure this gets called immediately prior to cell execution
  const notifyActiveCell = () => {
    if (disconnected) {
      Jupyter.notebook.events.unbind('create.Cell', notifyActiveCell);
      Jupyter.notebook.events.unbind('delete.Cell', notifyActiveCell);
      Jupyter.notebook.events.unbind('cut.Cell', notifyActiveCell);
      Jupyter.notebook.events.unbind('copy.Cell', notifyActiveCell);
      Jupyter.notebook.events.unbind('paste.Cell', notifyActiveCell);
      Jupyter.notebook.events.unbind('move_up.Cell', notifyActiveCell);
      Jupyter.notebook.events.unbind('move_down.Cell', notifyActiveCell);
    }
    onSelect(null, { cell: Jupyter.notebook.get_selected_cell() });
  };
  comm.on_msg((msg: any) => {
    // console.log('comm got msg: ');
    // console.log(msg.content.data)
    const payload = msg.content.data;
    if (disconnected || !(payload.success ?? true)) {
      return;
    }
    if (payload.type === 'establish') {
      isIpyflowCommConnected = true;
      Jupyter.notebook.events.on('create.Cell', notifyActiveCell);
      Jupyter.notebook.events.on('delete.Cell', notifyActiveCell);
      Jupyter.notebook.events.on('cut.Cell', notifyActiveCell);
      Jupyter.notebook.events.on('copy.Cell', notifyActiveCell);
      Jupyter.notebook.events.on('paste.Cell', notifyActiveCell);
      Jupyter.notebook.events.on('move_up.Cell', notifyActiveCell);
      Jupyter.notebook.events.on('move_down.Cell', notifyActiveCell);
      Jupyter.notebook.events.on('execute.CodeCell', onExecution);
      Jupyter.notebook.events.on('select.Cell', onSelect);
      const notifyContents = () => {
        if (disconnected) {
          return;
        }
        comm.send({
          type: 'notify_content_changed',
          cell_metadata_by_id: gatherCellMetadataById(Jupyter)
        });
        setTimeout(notifyContents, 2000);
      };
      notifyContents();
      Jupyter.keyboard_manager.command_shortcuts.add_shortcuts({
        'cmd-shift-enter': keybinding,
        'ctrl-shift-enter': keybinding
      });
      Jupyter.keyboard_manager.edit_shortcuts.add_shortcuts({
        'cmd-shift-enter': keybinding,
        'ctrl-shift-enter': keybinding
      });
      comm.send({
        type: 'compute_exec_schedule',
        cell_metadata_by_id: gatherCellMetadataById(Jupyter),
        is_reactively_executing: isReactivelyExecuting
      });
    } else if (payload.type === 'set_exec_mode') {
      isAltModeExecuting = false;
      lastExecutionMode = payload.exec_mode as string;
    } else if (payload.type === 'change_active_cell') {
      if (cellPendingExecutionIdx != null) {
        const idxToExec = cellPendingExecutionIdx;
        cellPendingExecution = cellPendingExecutionIdx = null;
        // 100 ms delay so that the dom has time to update css styling on the executing cell
        Jupyter.notebook.execute_cells([idxToExec]);
      }
    } else if (payload.type === 'compute_exec_schedule') {
      refreshNodeMapping(Jupyter);
      waitingCells = new Set((payload.waiting_cells ?? []) as string[]);
      readyCells = new Set((payload.ready_cells ?? []) as string[]);
      newReadyCells = new Set([
        ...newReadyCells,
        ...(payload.new_ready_cells as string[])
      ]);
      forcedReactiveCells = new Set([
        ...forcedReactiveCells,
        ...(payload.forced_reactive_cells as string[])
      ]);
      waiterLinks = payload.waiter_links as { [id: string]: string[] };
      readyMakerLinks = payload.ready_maker_links as { [id: string]: string[] };
      cellPendingExecution = null;
      cellPendingExecutionIdx = null;
      const exec_mode = payload.exec_mode as string;
      isReactivelyExecuting =
        isReactivelyExecuting ||
        ((payload?.is_reactively_executing as boolean) ?? false);
      const flow_order = payload.flow_order;
      const exec_schedule = payload.exec_schedule;
      lastExecutionMode = exec_mode;
      lastExecutionHighlights = payload.highlights as Highlights;
      const lastExecutedCellId = payload.last_executed_cell_id as string;
      executedReactiveReadyCells.add(lastExecutedCellId);
      const last_execution_was_error = payload.last_execution_was_error as boolean;
      if (!last_execution_was_error) {
        let loopBreak = false;
        let lastExecutedCellIdSeen = false;
        Jupyter.notebook.get_cells().forEach((cell: any, idx: number) => {
          if (loopBreak) {
            return;
          }
          if (!lastExecutedCellIdSeen) {
            lastExecutedCellIdSeen = cell.id === lastExecutedCellId;
            if (flow_order === 'in_order' || exec_schedule === 'strict') {
              return;
            }
          }
          if (
            cell.cell_type !== 'code' ||
            executedReactiveReadyCells.has(cell.id)
          ) {
            return;
          }
          if (!newReadyCells.has(cell.id)) {
            return;
          }
          if (!forcedReactiveCells.has(cell.id) && exec_mode !== 'reactive') {
            return;
          }
          if (cellPendingExecution == null) {
            if (activeCellToReturnToAfterReactiveExecution == null) {
              activeCellToReturnToAfterReactiveExecution = activeCell;
            }
            cellPendingExecution = cell;
            cellPendingExecutionIdx = idx;
            // break early if using one of the order-based semantics
            if (flow_order === 'in_order' || exec_schedule === 'strict') {
              loopBreak = true;
              return;
            }
          } else if (cell.input_prompt_number == null) {
            // pass
          } else if (
            cell.input_prompt_number < cellPendingExecution.input_prompt_number
          ) {
            // otherwise, execute in order of earliest execution counter
            cellPendingExecution = cell;
          }
        });
      }
      if (cellPendingExecution == null) {
        if (isReactivelyExecuting) {
          if (lastExecutionHighlights === 'reactive') {
            readyCells = executedReactiveReadyCells;
          }
          if (activeCellToReturnToAfterReactiveExecution != null) {
            Jupyter.notebook.events.trigger('select.Cell', {
              cell: activeCellToReturnToAfterReactiveExecution
            });
            activeCellToReturnToAfterReactiveExecution = null;
          }
          comm.send({ type: 'reactivity_cleanup' });
        }
        forcedReactiveCells = new Set();
        newReadyCells = new Set();
        executedReactiveReadyCells = new Set();
        updateUI(Jupyter);
        isReactivelyExecuting = false;
        if (lastExecutionMode === 'reactive') {
          isAltModeExecuting = false;
        }
        notifyActiveCell();
      } else {
        isReactivelyExecuting = true;
        updateUI(Jupyter);
        comm.send({
          type: 'change_active_cell',
          active_cell_id: cellPendingExecution.id,
          active_cell_order_idx: cellPendingExecutionIdx
        });
      }
    }
  });
  return () => {
    comm.close();
    disconnected = true;
    clearCellState(Jupyter);
    if (isIpyflowCommConnected) {
      Jupyter.keyboard_manager.command_shortcuts.remove_shortcut(
        'cmd-shift-enter'
      );
      Jupyter.keyboard_manager.command_shortcuts.remove_shortcut(
        'ctrl-shift-enter'
      );
      Jupyter.keyboard_manager.edit_shortcuts.remove_shortcut(
        'cmd-shift-enter'
      );
      Jupyter.keyboard_manager.edit_shortcuts.remove_shortcut(
        'ctrl-shift-enter'
      );
    }
    isIpyflowCommConnected = false;
  };
}

__non_webpack_require__(
  ['base/js/namespace', 'notebook/js/codecell'],
  (Jupyter: any, code_cell: any) => {
    // console.log('This is the current notebook application instance:', Jupyter.notebook);
    let commDisconnectHandler = function() {
      // nothing to do by default
    };

    const registerCommTarget = () => {
      Jupyter.notebook.kernel.comm_manager.register_target(
        'ipyflow-client',
        (comm: any, _open_msg: any) => {
          comm.on_msg((msg: any) => {
            const payload = msg.content.data;
            if (!(payload.success ?? true)) {
              return;
            }
            if (payload.type === 'unestablish') {
              commDisconnectHandler();
            } else if (payload.type === 'establish') {
              commDisconnectHandler();
              commDisconnectHandler = connectToComm(Jupyter, code_cell);
            }
          });
          commDisconnectHandler();
          commDisconnectHandler = connectToComm(Jupyter, code_cell);
        }
      );
    };

    Jupyter.notebook.events.on('spec_changed.Kernel', () => {
      // console.log('kernel changed');
      registerCommTarget();
      commDisconnectHandler();
    });
    Jupyter.notebook.events.on('kernel_restarting.Kernel', () => {
      // console.log('kernel changed');
      commDisconnectHandler();
    });
    Jupyter.notebook.events.on('kernel_ready.Kernel', () => {
      registerCommTarget();
      commDisconnectHandler = connectToComm(Jupyter, code_cell);
    });
  }
);
