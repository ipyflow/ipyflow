from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Tuple, Union
    from nbsafety.analysis.attr_symbols import AttrSubSymbolChain
    SymbolRef = Union[str, AttrSubSymbolChain]
    SupportedIndexType = Union[str, int, Tuple[Union[str, int], ...]]
