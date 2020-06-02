from typing import TYPE_CHECKING
from ..utils.mixins import CommonEqualityMixin

if TYPE_CHECKING:
    from typing import Union
    from .attr_symbols import AttrSubSymbolChain


class SymbolRef(CommonEqualityMixin):
    def __init__(self, ref: 'Union[str, AttrSubSymbolChain]'):
        self.symbol = ref

    def __hash__(self):
        return hash((self.symbol))

    def __str__(self):
        return str(self.symbol)

    def __repr__(self):
        return str(self)
