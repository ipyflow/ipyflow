
var NbsafetyPageObject = require('../src/nbsafety.po.js');
var nbsafetyPO;

describe('(Python) cell highlights', function () {
    beforeEach(function () {
        nbsafetyPO = new NbsafetyPageObject();
    });

    afterEach(function () {
        nbsafetyPO.closeAndHaltNotebook();
    });

    describe('Dependency mutated', function () {
        it('Should highlight stale input / output cells after dependency mutated', function () {
            nbsafetyPO.runNotebookByUrl('/test/notebooks/simplest.ipynb');
            nbsafetyPO.runCodeCellByIndex(0);
            var refresherCodeCell = nbsafetyPO.runCodeCellByIndex(1);
            var printCodeCell = nbsafetyPO.runCodeCellByIndex(2);
            nbsafetyPO.runCodeCellByIndex(3);
            expect(refresherCodeCell).toHaveClass('refresher-input-cell');
            expect(refresherCodeCell).toHaveClass('fresh-cell');
            expect(printCodeCell).toHaveClass('stale-cell');
            expect(printCodeCell).toHaveClass('fresh-cell');

            nbsafetyPO.runCodeCellByIndex(1);
            expect(refresherCodeCell).not.toHaveClass('refresher-input-cell');
            expect(refresherCodeCell).not.toHaveClass('fresh-cell');
            expect(printCodeCell).not.toHaveClass('stale-cell');
            expect(printCodeCell).toHaveClass('fresh-cell');

            nbsafetyPO.runCodeCellByIndex(2);
            expect(printCodeCell).not.toHaveClass('stale-cell');
            expect(printCodeCell).not.toHaveClass('fresh-cell');
        });
    });

    describe('Hover over highlight', function () {
        it('Should highlight linked stale / refresher cell(s) when hovering over a cell highlight', function () {
            nbsafetyPO.runNotebookByUrl('/test/notebooks/chain.ipynb');
            nbsafetyPO.runCodeCellByIndex(0);
            var refresherCodeCell = nbsafetyPO.runCodeCellByIndex(1);
            nbsafetyPO.runCodeCellByIndex(2);
            nbsafetyPO.runCodeCellByIndex(3);
            var lastInChain = nbsafetyPO.runCodeCellByIndex(4);
            nbsafetyPO.runCodeCellByIndex(5);
            expect(refresherCodeCell).toHaveClass('refresher-input-cell');
            expect(refresherCodeCell).toHaveClass('fresh-cell');
            expect(lastInChain).toHaveClass('stale-cell');
            expect(lastInChain).toHaveClass('fresh-cell');

            var linkedStale = nbsafetyPO.getInputCollapserChildByIndex(4);
            var linkedRefresher = nbsafetyPO.getInputCollapserChildByIndex(1);
            linkedStale.moveTo();
            expect(linkedRefresher).toHaveClass('linked-refresher');
            browser.pause(500);
            linkedRefresher.moveTo();
            expect(linkedStale).toHaveClass('linked-stale');
            browser.pause(500);
        });
    });
});
