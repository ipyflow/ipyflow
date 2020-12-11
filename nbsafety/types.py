from typing import Tuple, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from nbsafety.analysis.attr_symbols import AttrSubSymbolChain
    SymbolRef = Union[str, AttrSubSymbolChain]
    SupportedIndexType = Union[str, int, Tuple[Union[str, int], ...]]