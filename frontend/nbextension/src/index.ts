
import '../style/index.css';
// import "jqueryui";

const staleClass = 'stale-cell';
const staleOutputClass = 'stale-output-cell';
const refresherClass = 'refresher-cell';
const refresherInputClass = 'refresher-input-cell';
const linkedStaleInputClass = 'linked-stale-input';
const linkedStaleOutputClass = 'linked-stale-output';
const linkedRefresherClass = 'linked-refresher';

const cleanup = new Event('cleanup');

const getCellInputSection = (elem: HTMLElement) => {
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
};

const getCellOutputSection = (elem: HTMLElement) => {
    if (elem === null) {
        return null;
    }
    if (elem.children.item(1) === null) {
        return null;
    }
    return elem.children.item(1).firstElementChild;
};

const attachCleanupListener = (elem: any, evt: "mouseover" | "mouseout", listener: any) => {
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
    if (elem === null) {
        return;
    }
    const listener = () => {
        linkedElem.classList[add_or_remove](css);
    };
    attachCleanupListener(elem, evt, listener);
};

const addStaleOutputInteractions = (elem: HTMLElement) => {
    addStaleOutputInteraction(
        getCellInputSection(elem), elem, 'mouseover', 'add', linkedStaleOutputClass
    );
    addStaleOutputInteraction(
        getCellInputSection(elem), elem, 'mouseout', 'remove', linkedStaleOutputClass
    );

    addStaleOutputInteraction(
        getCellOutputSection(elem), elem, 'mouseover', 'add', linkedRefresherClass
    );
    addStaleOutputInteraction(
        getCellOutputSection(elem), elem, 'mouseout', 'remove', linkedRefresherClass
    );
};

 const addUnsafeCellInteraction = (elem: Element, linkedElems: [string],
                                   cellsById: {[id: string]: HTMLElement},
                                   evt: "mouseover" | "mouseout",
                                   add_or_remove: "add" | "remove",
                                   css: string) => {
     const listener = () => {
         for (const linkedId of linkedElems) {
             cellsById[linkedId].classList[add_or_remove](css);
         }
     };
     elem.addEventListener(evt, listener);
     attachCleanupListener(elem, evt, listener);
 };

const clearCellState = (Jupyter: any) => {
    Jupyter.notebook.get_cells().forEach((cell: any, idx: any) => {
        cell.element[0].classList.remove(staleClass);
        cell.element[0].classList.remove(refresherClass);
        cell.element[0].classList.remove(staleOutputClass);
        cell.element[0].classList.remove(refresherInputClass);
        cell.element[0].classList.remove(linkedStaleInputClass);
        cell.element[0].classList.remove(linkedStaleOutputClass);
        cell.element[0].classList.remove(linkedRefresherClass);

        const cellInput = getCellInputSection(cell.element[0]);
        if (cellInput !== null) {
            cellInput.dispatchEvent(cleanup);
        }

        const cellOutput = getCellOutputSection(cell.element[0]);
        if (cellOutput !== null) {
            cellOutput.dispatchEvent(cleanup);
        }
    });
};

const gatherCellContentsById = (Jupyter: any) => {
    const content_by_cell_id: {[id: string]: string} = {};
    Jupyter.notebook.get_cells().forEach((cell: any) => {
        if (cell.cell_type !== 'code') {
            return;
        }
        content_by_cell_id[cell.cell_id] = cell.get_text();
    });
    return content_by_cell_id;
}

const connectToComm = (Jupyter: any) => {
    const comm = Jupyter.notebook.kernel.comm_manager.new_comm('nbsafety');
    const onExecution = (evt: any, data: {cell: any}) => {
        if (data.cell.notebook !== Jupyter.notebook) {
            return;
        }
        // console.log(data.cell);
        data.cell.element[0].classList.remove(staleOutputClass);
        data.cell.element[0].classList.remove(refresherInputClass);
        comm.send({
            type: 'cell_freshness',
            executed_cell_id: data.cell.cell_id,
            content_by_cell_id: gatherCellContentsById(Jupyter)
        });
        // console.log(evt);
    };
    comm.on_msg((msg: any) => {
        // console.log('comm got msg: ');
        // console.log(msg.content.data)
        if (msg.content.data.type == 'establish') {
            Jupyter.notebook.events.on('execute.CodeCell', onExecution);
        } else if (msg.content.data.type === 'cell_freshness') {
            clearCellState(Jupyter);
            const staleInputCells: any = msg.content.data['stale_input_cells'];
            const staleOutputCells: any = msg.content.data['stale_output_cells'];
            const staleLinks: any = msg.content.data['stale_links'];
            const refresherLinks: any = msg.content.data['refresher_links'];
            const cellsById: {[id: string]: HTMLElement} = {};
            Jupyter.notebook.get_cells().forEach((cell: any) => {
                cellsById[cell.cell_id] = cell.element[0];
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
                        getCellInputSection(elem), staleLinks[id], cellsById,
                        'mouseover', 'add', linkedRefresherClass
                    );

                    addUnsafeCellInteraction(
                        getCellOutputSection(elem), staleLinks[id], cellsById,
                        'mouseover', 'add', linkedRefresherClass
                    );

                    addUnsafeCellInteraction(
                        getCellInputSection(elem), staleLinks[id], cellsById,
                        'mouseout', 'remove', linkedRefresherClass
                    );

                    addUnsafeCellInteraction(
                        getCellOutputSection(elem), staleLinks[id], cellsById,
                        'mouseout', 'remove', linkedRefresherClass
                    );
                }

                if (refresherLinks.hasOwnProperty(id)) {
                    elem.classList.add(refresherClass);
                    elem.classList.add(staleOutputClass);
                    addStaleOutputInteractions(elem);
                    addUnsafeCellInteraction(
                        getCellInputSection(elem), refresherLinks[id], cellsById,
                        'mouseover', 'add', linkedStaleInputClass
                    );

                    addUnsafeCellInteraction(
                        getCellInputSection(elem), refresherLinks[id], cellsById,
                        'mouseover', 'add', linkedStaleOutputClass,
                    );

                    addUnsafeCellInteraction(
                        getCellInputSection(elem), refresherLinks[id], cellsById,
                        'mouseout', 'remove', linkedStaleInputClass
                    );

                    addUnsafeCellInteraction(
                        getCellInputSection(elem), refresherLinks[id], cellsById,
                        'mouseout', 'remove', linkedStaleOutputClass
                    );
                }
            }
        }
    });
    comm.send({
        type: 'cell_freshness',
        content_by_cell_id: gatherCellContentsById(Jupyter)
    });
    return () => {
        clearCellState(Jupyter);
        Jupyter.notebook.events.unbind('execute.CodeCell', onExecution);
    };
}

__non_webpack_require__([
  'base/js/namespace'
], function load_ipython_extension(Jupyter: any) {
    // console.log('This is the current notebook application instance:', Jupyter.notebook);
    Jupyter.notebook.events.on('kernel_ready.Kernel', () => {
        const commDisconnectHandler = connectToComm(Jupyter);
        Jupyter.notebook.events.on('spec_changed.Kernel', () => {
            // console.log('kernel changed');
            commDisconnectHandler();
        });
    });
});