from typing import TYPE_CHECKING
from ..utils.mixins import CommonEqualityMixin

if TYPE_CHECKING:
    from typing import Union
    from .attr_symbols import AttrSubSymbolChain


class SymbolRef(CommonEqualityMixin):
    def __init__(self, ref: 'Union[str, AttrSubSymbolChain]', deep=False):
        self.symbol = ref
        self.deep = deep

    def __hash__(self):
        return hash((self.symbol, self.deep))

    def __str__(self):
        if self.deep:
            return f'<Deep ref of {self.symbol}>'
        else:
            return str(self.symbol)
