import { Cell } from '@jupyterlab/cells';
import { Notebook } from '@jupyterlab/notebook';

import {
  classicColorsClass,
  linkedReadyMakerClass,
  linkedWaitingClass,
  readyClass,
  readyMakingClass,
  readyMakingInputClass,
  waitingClass,
  sliceClass,
  executeSliceClass,
} from './classes';

const cleanup = new Event('cleanup');

export function getJpInputCollapser(elem: HTMLElement): Element | null {
  if (elem === null || elem === undefined) {
    return null;
  }
  const child = elem.children.item(1);
  if (child === null) {
    return null;
  }
  return child.firstElementChild;
}

export function getJpOutputCollapser(elem: HTMLElement): Element | null {
  if (elem === null || elem === undefined) {
    return null;
  }
  const child = elem.children.item(2);
  if (child === null) {
    return null;
  }
  return child.firstElementChild;
}

export function attachCleanupListener(
  elem: Element,
  evt: 'mouseover' | 'mouseout',
  listener: any
): void {
  const cleanupListener = () => {
    elem.removeEventListener(evt, listener);
    elem.removeEventListener('cleanup', cleanupListener);
  };
  elem.addEventListener(evt, listener);
  elem.addEventListener('cleanup', cleanupListener);
}

export function addWaitingOutputInteraction(
  elem: Element,
  linkedElem: Element,
  evt: 'mouseover' | 'mouseout',
  add_or_remove: 'add' | 'remove',
  css: string
): void {
  if (elem === null || linkedElem === null) {
    return;
  }
  const listener = () => {
    linkedElem.firstElementChild.classList[add_or_remove](css);
  };
  attachCleanupListener(elem, evt, listener);
}

export function addWaitingOutputInteractions(
  elem: HTMLElement,
  linkedInputClass: string
): void {
  addWaitingOutputInteraction(
    getJpInputCollapser(elem),
    getJpOutputCollapser(elem),
    'mouseover',
    'add',
    linkedWaitingClass
  );
  addWaitingOutputInteraction(
    getJpInputCollapser(elem),
    getJpOutputCollapser(elem),
    'mouseout',
    'remove',
    linkedWaitingClass
  );

  addWaitingOutputInteraction(
    getJpOutputCollapser(elem),
    getJpInputCollapser(elem),
    'mouseover',
    'add',
    linkedInputClass
  );
  addWaitingOutputInteraction(
    getJpOutputCollapser(elem),
    getJpInputCollapser(elem),
    'mouseout',
    'remove',
    linkedInputClass
  );
}

export function clearCellState(notebook: Notebook): void {
  notebook.widgets.forEach((cell) => {
    cell.node.classList.remove(classicColorsClass);
    cell.node.classList.remove(waitingClass);
    cell.node.classList.remove(readyMakingClass);
    cell.node.classList.remove(readyClass);
    cell.node.classList.remove(readyMakingInputClass);
    cell.node.classList.remove(sliceClass);
    cell.node.classList.remove(executeSliceClass);

    // clear any old event listeners
    const inputCollapser = getJpInputCollapser(cell.node);
    if (inputCollapser !== null) {
      inputCollapser.firstElementChild.classList.remove(linkedWaitingClass);
      inputCollapser.firstElementChild.classList.remove(linkedReadyMakerClass);
      inputCollapser.dispatchEvent(cleanup);
    }

    const outputCollapser = getJpOutputCollapser(cell.node);
    if (outputCollapser !== null) {
      outputCollapser.firstElementChild.classList.remove(linkedWaitingClass);
      outputCollapser.firstElementChild.classList.remove(linkedReadyMakerClass);
      outputCollapser.dispatchEvent(cleanup);
    }
  });
}

export function addUnsafeCellInteraction(
  elem: Element,
  linkedElems: string[],
  cellsById: { [id: string]: Cell },
  collapserFun: (elem: HTMLElement) => Element,
  evt: 'mouseover' | 'mouseout',
  add_or_remove: 'add' | 'remove',
  waitingCells: Set<string>
): void {
  if (elem === null) {
    return;
  }
  const listener = () => {
    for (const linkedId of linkedElems) {
      let css = linkedReadyMakerClass;
      if (waitingCells.has(linkedId)) {
        css = linkedWaitingClass;
      }
      const collapser = collapserFun(cellsById[linkedId].node);
      if (collapser === null || collapser.firstElementChild === null) {
        return;
      }
      collapser.firstElementChild.classList[add_or_remove](css);
    }
  };
  elem.addEventListener(evt, listener);
  attachCleanupListener(elem, evt, listener);
}
