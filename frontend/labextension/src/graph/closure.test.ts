import { describe, expect, it } from 'vitest';

import {
  computeRawTransitiveClosure,
  computeTopoOrderIdx,
  IClosureContext,
} from './closure';

function makeCtx(overrides: Partial<IClosureContext> = {}): IClosureContext {
  return {
    cellParents: {},
    cellChildren: {},
    executedCells: new Set(),
    readyCells: new Set(),
    waitingCells: new Set(),
    dirtyCells: new Set(),
    staleParents: {},
    staleParentsByExecutedCellByChild: {},
    staleParentsByChildByExecutedCell: {},
    settings: {},
    ...overrides,
  };
}

const sorted = (s: Set<string>): string[] => Array.from(s).sort();

describe('computeRawTransitiveClosure', () => {
  it('follows children down a linear chain (inclusive)', () => {
    const ctx = makeCtx({ cellChildren: { a: ['b'], b: ['c'] } });
    expect(sorted(computeRawTransitiveClosure(ctx, ['a']))).toEqual([
      'a',
      'b',
      'c',
    ]);
  });

  it('excludes the start cells when inclusive=false', () => {
    const ctx = makeCtx({ cellChildren: { a: ['b'], b: ['c'] } });
    expect(sorted(computeRawTransitiveClosure(ctx, ['a'], false))).toEqual([
      'b',
      'c',
    ]);
  });

  it('follows parents up the chain when parents=true', () => {
    const ctx = makeCtx({ cellParents: { c: ['b'], b: ['a'] } });
    expect(sorted(computeRawTransitiveClosure(ctx, ['c'], true, true))).toEqual(
      ['a', 'b', 'c'],
    );
  });

  it('dedupes diamond-shaped graphs', () => {
    const ctx = makeCtx({
      cellChildren: { a: ['b', 'c'], b: ['d'], c: ['d'] },
    });
    expect(sorted(computeRawTransitiveClosure(ctx, ['a']))).toEqual([
      'a',
      'b',
      'c',
      'd',
    ]);
  });

  it('terminates on cycles', () => {
    const ctx = makeCtx({ cellChildren: { a: ['b'], b: ['a'] } });
    expect(sorted(computeRawTransitiveClosure(ctx, ['a']))).toEqual(['a', 'b']);
  });

  it('handles empty and unknown start ids', () => {
    const ctx = makeCtx({ cellChildren: { a: ['b'] } });
    expect(sorted(computeRawTransitiveClosure(ctx, []))).toEqual([]);
    expect(sorted(computeRawTransitiveClosure(ctx, ['zzz']))).toEqual(['zzz']);
  });

  it('supports multiple start cells', () => {
    const ctx = makeCtx({ cellChildren: { a: ['b'], x: ['y'] } });
    expect(sorted(computeRawTransitiveClosure(ctx, ['a', 'x']))).toEqual([
      'a',
      'b',
      'x',
      'y',
    ]);
  });

  it('pulls in a stale parent when pull_reactive_updates is on', () => {
    const base = {
      cellChildren: { p: ['c'] },
      cellParents: { c: ['p'] },
      settings: { pull_reactive_updates: true },
    };
    // With c marked as having a stale parent p, the reactive pull reaches p.
    const withStale = makeCtx({ ...base, staleParents: { c: ['p'] } });
    expect(sorted(computeRawTransitiveClosure(withStale, ['c']))).toEqual([
      'c',
      'p',
    ]);
    // Without the stale-parent link, p is not pulled in.
    const withoutStale = makeCtx(base);
    expect(sorted(computeRawTransitiveClosure(withoutStale, ['c']))).toEqual([
      'c',
    ]);
  });

  it('pulls in a stale parent whose edge was pruned from cellParents', () => {
    // The kernel dedupes cellParents for display (only the last writer of a
    // symbol survives), but a pruned parent can still be the cell that resets
    // the mutated state -- e.g. `a = [...]` above an `a, b = b, a` swap.
    const ctx = makeCtx({
      cellChildren: { a0: [], a1: ['swap'], swap: ['use'] },
      cellParents: { swap: ['a1'], use: ['swap'] },
      staleParents: { swap: ['a0', 'a1'], use: ['swap'] },
      settings: { pull_reactive_updates: true },
    });
    expect(sorted(computeRawTransitiveClosure(ctx, ['use']))).toEqual([
      'a0',
      'a1',
      'swap',
      'use',
    ]);
  });

  it('pulls reactive updates across cousins and terminates', () => {
    const ctx = makeCtx({
      cellChildren: { a: ['b'], a2: ['b'] },
      cellParents: { b: ['a', 'a2'] },
      staleParents: { b: ['a2'] },
      settings: {
        pull_reactive_updates: true,
        push_reactive_updates_to_cousins: true,
      },
    });
    expect(sorted(computeRawTransitiveClosure(ctx, ['a']))).toEqual([
      'a',
      'a2',
      'b',
    ]);
  });
});

describe('computeTopoOrderIdx', () => {
  const allCode = () => true;

  it('orders a linear chain parents-before-children', () => {
    const order = computeTopoOrderIdx(
      ['a', 'b', 'c'],
      { a: ['b'], b: ['c'] },
      allCode,
    );
    expect(order).toEqual({ a: 0, b: 1, c: 2 });
  });

  it('produces a valid topological order for a diamond', () => {
    const order = computeTopoOrderIdx(
      ['a', 'b', 'c', 'd'],
      { a: ['b', 'c'], b: ['d'], c: ['d'] },
      allCode,
    );
    expect(order.a).toBeLessThan(order.b);
    expect(order.a).toBeLessThan(order.c);
    expect(order.b).toBeLessThan(order.d);
    expect(order.c).toBeLessThan(order.d);
  });

  it('skips non-code cells', () => {
    const isCode = (id: string) => id !== 'b';
    const order = computeTopoOrderIdx(
      ['a', 'b', 'c'],
      { a: ['b'], b: ['c'] },
      isCode,
    );
    expect('b' in order).toBe(false);
    expect('a' in order).toBe(true);
  });
});
