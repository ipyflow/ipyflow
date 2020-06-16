
var NbsafetyPageObject = require('../src/nbsafety.po.js');
var nbsafetyPO;

describe('(Python) stale cell coloring', function () {
    beforeAll(function () {
        nbsafetyPO = new NbsafetyPageObject();
        nbsafetyPO.runNotebookByUrl('/test/notebooks/simplest.ipynb');
    });

    afterAll(function () {
        nbsafetyPO.closeAndHaltNotebook();
    });

    describe('Dependency mutated', function () {
        it('Should highlight stale input / output cells after dependency mutated', function () {
            nbsafetyPO.runCodeCellByIndex(0);
            var refresherCodeCell = nbsafetyPO.runCodeCellByIndex(1);
            var printCodeCell = nbsafetyPO.runCodeCellByIndex(2);
            nbsafetyPO.runCodeCellByIndex(3);
            expect(refresherCodeCell).toHaveClass('refresher-input-cell');
            expect(refresherCodeCell).toHaveClass('stale-output-cell');
            expect(printCodeCell).toHaveClass('stale-cell');
            expect(printCodeCell).toHaveClass('stale-output-cell');

            nbsafetyPO.runCodeCellByIndex(1);
            expect(refresherCodeCell).not.toHaveClass('refresher-input-cell');
            expect(refresherCodeCell).not.toHaveClass('stale-output-cell');
            expect(printCodeCell).not.toHaveClass('stale-cell');
            expect(printCodeCell).toHaveClass('stale-output-cell');

            nbsafetyPO.runCodeCellByIndex(2);
            expect(printCodeCell).not.toHaveClass('stale-cell');
            expect(printCodeCell).not.toHaveClass('stale-output-cell');
        });
    });
});
