# -*- coding: future_annotations -*-
from typing import TYPE_CHECKING
from nbsafety.analysis.symbol_ref import SymbolRef
from nbsafety.utils import CommonEqualityMixin

if TYPE_CHECKING:
    from nbsafety.data_model.data_symbol import DataSymbol


class LiveSymbolRef(CommonEqualityMixin):
    def __init__(self, ref: SymbolRef, timestamp: int) -> None:
        self.ref = ref
        self.timestamp = timestamp

    def __hash__(self):
        return hash((self.ref, self.timestamp))


class LiveDataSymbol:
    def __init__(self, dsym: DataSymbol, is_called: bool, is_deep: bool, is_reactive: bool) -> None:
        self.dsym = dsym
        self.is_called = is_called
        self.is_deep = is_deep
        self.is_reactive = is_reactive

    @property
    def is_shallow(self) -> bool:
        return not self.is_deep
