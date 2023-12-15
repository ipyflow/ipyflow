from typing import Any, Tuple, Type, TypeVar, Union

from ipyflow.data_model.timestamp import Timestamp

IdType = Union[str, int]
SupportedIndexType = Union[
    str, int, bool, None, Tuple[Union[str, int, bool, None], ...]
]
TimestampOrCounter = TypeVar("TimestampOrCounter", Timestamp, int)

IMMUTABLE_PRIMITIVE_TYPES = (
    bytes,
    bytearray,
    float,
    frozenset,
    int,
    str,
    tuple,
)


class SubscriptIndices:
    types: Tuple[Type[Any], ...] = (str, int, bool, type(None))
