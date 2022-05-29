# -*- coding: utf-8 -*-
from typing import Any, cast
from ipyflow.data_model.data_symbol import DataSymbol


def lift(sym: Any) -> DataSymbol:
    return cast(DataSymbol, sym)
