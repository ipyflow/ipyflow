from typing import Tuple, TypeVar, Union
from ipyflow.data_model.timestamp import Timestamp

CellId = Union[str, int]
SupportedIndexType = Union[str, int, Tuple[Union[str, int], ...]]
TimestampOrCounter = TypeVar("TimestampOrCounter", Timestamp, int)