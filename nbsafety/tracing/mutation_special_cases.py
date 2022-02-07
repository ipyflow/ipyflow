# -*- coding: utf-8 -*-
from typing import Set, Tuple


METHODS_WITH_MUTATION_EVEN_FOR_NON_NULL_RETURN: Set[Tuple[int, str]] = set()
METHODS_WITHOUT_MUTATION_EVEN_FOR_NULL_RETURN: Set[Tuple[int, str]] = set()

try:
    import time

    time_id = id(time)
    METHODS_WITHOUT_MUTATION_EVEN_FOR_NULL_RETURN.add((time_id, "sleep"))
except ImportError:
    pass

try:
    import pylab

    pylab_id = id(pylab)
    METHODS_WITH_MUTATION_EVEN_FOR_NON_NULL_RETURN.add((pylab_id, "figure"))
    METHODS_WITHOUT_MUTATION_EVEN_FOR_NULL_RETURN.add((pylab_id, "show"))
    METHODS_WITHOUT_MUTATION_EVEN_FOR_NULL_RETURN.add((pylab_id, "plot"))
except ImportError:
    pass

try:
    import matplotlib.pyplot as plt

    plt_id = id(plt)
    METHODS_WITH_MUTATION_EVEN_FOR_NON_NULL_RETURN.add((plt_id, "figure"))
    METHODS_WITHOUT_MUTATION_EVEN_FOR_NULL_RETURN.add((plt_id, "show"))
    METHODS_WITHOUT_MUTATION_EVEN_FOR_NULL_RETURN.add((plt_id, "plot"))
except ImportError:
    pass

try:
    import d2l.torch as d2l

    d2l_id = id(d2l)
    METHODS_WITHOUT_MUTATION_EVEN_FOR_NULL_RETURN.add((d2l_id, "plot"))
except ImportError:
    pass
