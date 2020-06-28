
import '../style/index.css';
// import "jqueryui";

const staleClass = 'stale-cell';
const staleOutputClass = 'stale-output-cell';
const refresherClass = 'refresher-cell';
const refresherInputClass = 'refresher-input-cell';
// const linkedStaleClass = 'linked-stale';
// const linkedRefresherClass = 'linked-refresher';

const clearCellState = (Jupyter: any) => {
    Jupyter.notebook.get_cells().forEach((cell: any, idx: any) => {
        cell.element[0].classList.remove(staleClass);
        cell.element[0].classList.remove(refresherClass);
        cell.element[0].classList.remove(staleOutputClass);
        cell.element[0].classList.remove(refresherInputClass);
    });
};

const connectToComm = (Jupyter: any) => {
    const comm = Jupyter.notebook.kernel.comm_manager.new_comm('nbsafety');
    const onExecution = (evt: any, data: {cell: any}) => {
        if (data.cell.notebook !== Jupyter.notebook) {
            return;
        }
        console.log(data.cell);
        const cells = Jupyter.notebook.get_cells();
        const content_by_cell_id: {[id: string]: string} = {};
        // data.cell.element[0].classList.remove(staleOutputClass);
        // data.cell.element[0].classList.remove(refresherInputClass);
        cells.forEach((cell: any, idx: any) => {
            if (cell.cell_type !== 'code') {
                return;
            }
            content_by_cell_id[cell.cell_id] = cell.get_text();
        });
        const payload = {
            type: 'cell_freshness',
            executed_cell_id: data.cell.cell_id,
            content_by_cell_id: content_by_cell_id
        };
        comm.send(payload);
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
            Jupyter.notebook.get_cells().forEach((cell: any, idx: any) => {
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

                    // addStaleOutputInteractions(elem);
                }

                if (staleLinks.hasOwnProperty(id)) {
                    // TODO: add unsafe cell interactions
                }

                if (refresherLinks.hasOwnProperty(id)) {
                    elem.classList.add(refresherClass);
                    elem.classList.add(staleOutputClass);
                    // TODO: add unsafe cell interactions
                }
            }
        }
    });
    return () => Jupyter.notebook.events.unbind('execute.CodeCell', onExecution);
}

__non_webpack_require__([
  'base/js/namespace'
], function load_ipython_extension(Jupyter: any) {
    // console.log('This is the current notebook application instance:', Jupyter.notebook);
    // const commDisconnectHandler = connectToComm(Jupyter);
    connectToComm(Jupyter);
});