# -*- coding: utf-8 -*-
from types import ModuleType
from typing import TYPE_CHECKING, Type

from ipyflow.tracing.uninstrument import uninstrument

if TYPE_CHECKING:
    from pyspark.sql.udf import UserDefinedFunction


def patch_pyspark_udf(module: ModuleType) -> None:
    udf_cls: Type["UserDefinedFunction"] = module.UserDefinedFunction
    udf_cls_init = udf_cls.__init__

    def _patched_init(self_: "UserDefinedFunction", func, *args, **kwargs) -> None:
        uninstrumented = uninstrument(func)
        return udf_cls_init(
            self_, func if uninstrumented is None else uninstrumented, *args, **kwargs
        )

    udf_cls.__init__ = _patched_init
