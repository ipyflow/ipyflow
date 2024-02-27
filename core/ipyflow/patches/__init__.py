# -*- coding: utf-8 -*-
import logging
import sys
from types import ModuleType
from typing import Callable, Set, Tuple

from ipyflow.patches.cloudpickle_patch import patch_cloudpickle_function_reduce
from ipyflow.patches.pyspark_patch import patch_pyspark_udf

logger = logging.getLogger(__name__)

_predicate_patch_pairs: Tuple[
    Tuple[Callable[[str], bool], Callable[[ModuleType], None]], ...
] = (
    (
        lambda modname: modname.endswith("cloudpickle.cloudpickle_fast"),
        patch_cloudpickle_function_reduce,
    ),
    (lambda modname: modname == "pyspark.sql.udf", patch_pyspark_udf),
    (lambda modname: modname == "pyspark.sql.connect.udf", patch_pyspark_udf),
)

_patched_modules: Set[str] = set()


def apply_patches(modname: str, module: ModuleType) -> None:
    if modname in _patched_modules:
        return
    for predicate, patch in _predicate_patch_pairs:
        try:
            if predicate(modname):
                patch(module)
                _patched_modules.add(modname)
        except Exception:  # noqa
            logger.exception("Failed to apply patch to module %s", modname)


def patch_all() -> None:
    for modname, module in list(sys.modules.items()):
        apply_patches(modname, module)
