# -*- coding: future_annotations -*-
from typing import Any, Dict, List, Set, Tuple, Union

MAX_SIZE = 10**5

"""
This module helps us detect if it will be too expensive to perform
equality checking to see if a symbol's underlying value changed by
detecting if the size exceeds some semi-arbitrary upper bound MAX_SIZE.

All the methods return the actual size if this threshold is not exceeded;
otherwise the return float('inf').
"""


def sizeof_list_or_set_or_tuple(obj: Union[List[Any], Set[Any], Tuple[Any, ...]]) -> float:
    total_size: float = len(obj)
    for elt in obj:
        if total_size > MAX_SIZE:
            break
        total_size += sizeof(elt)
    return total_size if total_size <= MAX_SIZE else float('inf')


def sizeof_dict(obj: Dict[Any, Any]) -> float:
    total_size: float = len(obj)
    for k, v in obj.items():
        if total_size > MAX_SIZE:
            break
        total_size += sizeof(k) + sizeof(v)
    return total_size if total_size <= MAX_SIZE else float('inf')


def sizeof(obj: Any) -> float:
    sz: float = float('inf')
    if isinstance(obj, (int, float)):
        sz = 1
    elif isinstance(obj, str):
        sz = len(obj)
    elif isinstance(obj, (list, set, tuple)):
        sz = sizeof_list_or_set_or_tuple(obj)
    elif isinstance(obj, dict):
        sz = sizeof_dict(obj)
    return sz if sz <= MAX_SIZE else float('inf')
