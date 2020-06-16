/*
 *  Copyright 2017 TWO SIGMA OPEN SOURCE, LLC
 *
 *  Licensed under the Apache License, Version 2.0 (the "License");
 *  you may not use this file except in compliance with the License.
 *  You may obtain a copy of the License at
 *
 *         http://www.apache.org/licenses/LICENSE-2.0
 *
 *  Unless required by applicable law or agreed to in writing, software
 *  distributed under the License is distributed on an "AS IS" BASIS,
 *  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *  See the License for the specific language governing permissions and
 *  limitations under the License.
 */

// smacke: this code was originally copied. license + copyright preserved as originally stated.
// ref: https://github.com/twosigma/beakerx/

var LabPageObject = require('./lab.po.js').prototype;

function NbsafetyPageObject() {
  LabPageObject.constructor.apply(this, arguments);
  NbsafetyPageObject.prototype = Object.create(LabPageObject);


  this.runCodeCellByIndex = function (index) {
    var codeCell = this.getCodeCellByIndex(index);
    codeCell.scrollIntoView();
    codeCell.click();
    this.clickRunCell();
    this.kernelIdleIcon.waitForEnabled(120000);
    browser.pause(50);
    return codeCell;
  };

  this.runAndCheckOutputTextOfExecuteResult = function (cellIndex, expectedText) {
    this.runCellAndCheckTextHandleError(cellIndex, expectedText, this.getAllOutputsExecuteResult);
  };

  this.runAndCheckOutputTextOfStdout = function (cellIndex, expectedText) {
    this.runCellAndCheckTextHandleError(cellIndex, expectedText, this.getAllOutputsStdout);
  };

  this.runAndCheckOutputTextOfStderr = function (cellIndex, expectedText) {
    this.runCellAndCheckTextHandleError(cellIndex, expectedText, this.getAllOutputsStderr);
  };

  this.runAndCheckOutputTextOfWidget = function (cellIndex, expectedText) {
    this.runCellAndCheckTextHandleError(cellIndex, expectedText, this.getAllOutputsWidget);
  };

  this.runCellAndCheckTextHandleError = function (cellIndex, expectedText, getTextElements) {
    var resultTest;
    var codeCell;
    var attempt = 3;
    while (attempt > 0) {
      try {
        codeCell = this.runCodeCellByIndex(cellIndex);
        this.kernelIdleIcon.waitForEnabled();
        browser.pause(1000);
        resultTest = getTextElements(codeCell)[0].getText();
        attempt = 0;
      } catch (e) {
        attempt -= 1;
      }
    }
    expect(resultTest).toMatch(expectedText);
  };

  this.waitAndCheckOutputTextOfExecuteResult = function (cellIndex, expectedText, outputIndex) {
    this.waitAndCheckOutputText(cellIndex, expectedText, this.getAllOutputsExecuteResult, outputIndex);
  };

  this.waitAndCheckOutputTextOfStdout = function (cellIndex, expectedText, outputIndex) {
    this.waitAndCheckOutputText(cellIndex, expectedText, this.getAllOutputsStdout, outputIndex);
  };

  this.waitAndCheckOutputTextOfStderr = function (cellIndex, expectedText, outputIndex) {
    this.waitAndCheckOutputText(cellIndex, expectedText, this.getAllOutputsStderr, outputIndex);
  };

  this.waitAndCheckOutputTextOfWidget = function (cellIndex, expectedText, outputIndex) {
    this.waitAndCheckOutputText(cellIndex, expectedText, this.getAllOutputsWidget, outputIndex);
  };

  this.waitAndCheckOutputTextOfHtmlType = function (cellIndex, expectedText, outputIndex) {
    this.waitAndCheckOutputText(cellIndex, expectedText, this.getAllOutputsHtmlType, outputIndex);
  };

  this.waitAndCheckOutputText = function (index, expectedText, getTextElements, outputIndex) {
    if (!outputIndex) {
      outputIndex = 0;
    }
    var codeCell = this.getCodeCellByIndex(index);
    codeCell.scrollIntoView();
    browser.waitUntil(function () {
      var output = getTextElements(codeCell)[outputIndex];
      return output != null && output.isEnabled() && expectedText.test(output.getText());
    }, 50000, 'expected output toMatch ' + expectedText);
  };


  this.checkBrowserLogError = function (log_level) {
    var i = 0;
    var logMsgs = browser.log('browser').value;
    while (i < logMsgs.length) {
      if (logMsgs[i].level == log_level) {
        expect(logMsgs[i].message).not.toMatch(/./);
      }
      i += 1;
    }
  };

  this.checkKernelIdle = function () {
    return this.kernelIdleIcon.waitForEnabled();
  };

  this.setProperty = function (key, value) {
    browser.$('div#properties_property input[placeholder="name"]').setValue(key);
    browser.$('div#properties_property input[placeholder="value"]').setValue(value);
    browser.waitUntil(function () {
      var indicator = browser.$('span.saved');
      return indicator.isDisplayed();
    });
  };

  this.removeProperty = function () {
    var deleteButton = $('button > i.fa-times');
    deleteButton.click();
  };

  this.performRightClick = function (elem, x, y) {
    var result = browser.execute(function (webElem, offsetX, offsetY) {
      var datePosition = webElem.getBoundingClientRect();
      var clickEvent = new MouseEvent("contextmenu", {
        "view": window,
        "bubbles": true,
        "cancelable": false,
        'clientX': datePosition.left + offsetX,
        'clientY': datePosition.top + offsetY
      });
      webElem.dispatchEvent(clickEvent);
    }, elem, x, y);
    browser.pause(1000);
    return result;
  };

  this.performMouseMove = function (elem, x, y) {
    var result = browser.execute(function (webElem, offsetX, offsetY) {
      var datePosition = webElem.getBoundingClientRect();
      var clickEvent = new MouseEvent("mousemove", {
        "view": window,
        "bubbles": true,
        "cancelable": false,
        'clientX': datePosition.left + offsetX,
        'clientY': datePosition.top + offsetY
      });
      webElem.dispatchEvent(clickEvent);
    }, elem, x, y);
    browser.pause(1000);
    return result;
  };

};

module.exports = NbsafetyPageObject;