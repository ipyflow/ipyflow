# -*- coding: utf-8 -*-
import sys

from ipyflow.kernel.kernel import IPyflowKernel

__all__ = ["IPyflowKernel"]


# ref: https://mkennedy.codes/posts/python-gc-settings-change-this-and-make-your-app-go-20pc-faster/
def _set_gc_thresholds() -> None:
    import gc

    allocs, gen1, gen2 = gc.get_threshold()
    if allocs >= 50_000:
        # relaxed thresholds already set
        return

    # Clean up what might be garbage so far.
    gc.collect(2)

    if sys.version_info >= (3, 7):
        # Exclude current items from future GC.
        # only available in Python 3.7+
        gc.freeze()

    allocs = 50_000  # Start the GC sequence every 50K not 700 allocations.
    gen1 = gen1 * 2
    gen2 = gen2 * 2
    gc.set_threshold(allocs, gen1, gen2)


_set_gc_thresholds()
