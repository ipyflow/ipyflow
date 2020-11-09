
var NbsafetyPageObject = require('../src/nbsafety.po.js');
var nbsafetyPO;

describe('(Python) stale cells linked to refreshers', function () {
    beforeAll(function () {
        nbsafetyPO = new NbsafetyPageObject();
        nbsafetyPO.runNotebookByUrl('/test/notebooks/chain.ipynb');
    });

    afterAll(function () {
        nbsafetyPO.closeAndHaltNotebook();
    });

    describe('Dependency mutated', function () {
        it('Should highlight stale input / output cells after dependency mutated', function () {
            nbsafetyPO.runCodeCellByIndex(0);
            var refresherCodeCell = nbsafetyPO.runCodeCellByIndex(1);
            nbsafetyPO.runCodeCellByIndex(2);
            nbsafetyPO.runCodeCellByIndex(3);
            var lastInChain = nbsafetyPO.runCodeCellByIndex(4);
            nbsafetyPO.runCodeCellByIndex(5);
            expect(refresherCodeCell).toHaveClass('refresher-input-cell');
            expect(refresherCodeCell).toHaveClass('stale-output-cell');
            expect(lastInChain).toHaveClass('stale-cell');
            expect(lastInChain).toHaveClass('stale-output-cell');

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
