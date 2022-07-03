# -*- coding: utf-8 -*-
import time
from types import ModuleType
from typing import Dict, List, Set, Tuple

METHODS_WITH_MUTATION_EVEN_FOR_NON_NULL_RETURN: Set[Tuple[int, str]] = set()
# time is already imported
METHODS_WITHOUT_MUTATION_EVEN_FOR_NULL_RETURN: Set[Tuple[int, str]] = {(id(time), "sleep")}


_METHODS_WITH_MUTATION_EVEN_FOR_NON_NULL_RETURN_RAW: Dict[str, List[str]] = {
    "pylab": ["figure"],
    "matplotlib.pyplot": ["figure"],
}


_METHODS_WITHOUT_MUTATION_EVEN_FOR_NULL_RETURN_RAW: Dict[str, List[str]] = {
    "pylab": ["show", "plot"],
    "matplotlib.pyplot": ["show", "plot"],
    "d2l.torch": ["plot"],
}


def register_module_mutation_exceptions(module: ModuleType) -> None:
    for raw, registered in [
        (
            _METHODS_WITH_MUTATION_EVEN_FOR_NON_NULL_RETURN_RAW,
            METHODS_WITH_MUTATION_EVEN_FOR_NON_NULL_RETURN,
        ),
        (
            _METHODS_WITHOUT_MUTATION_EVEN_FOR_NULL_RETURN_RAW,
            METHODS_WITHOUT_MUTATION_EVEN_FOR_NULL_RETURN,
        ),
    ]:
        for excepted_method in raw.get(module.__name__, []):
            registered.add((id(module), excepted_method))
