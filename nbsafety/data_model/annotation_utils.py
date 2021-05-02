# -*- coding: future_annotations -*-
import typing
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, List, Iterable

_PRIMITIVE_TYPES = {int, float, str, type(None)}
_CONTAINER_TYPES = {
    dict: typing.Dict,
    list: typing.List,
    set: typing.Set,
    tuple: typing.Tuple,
}


def _get_contained_type_annotations(container: Iterable[Any]) -> List:
    annotations = []
    for obj in container:
        annotations.append(get_type_annotation(obj))
    return annotations


def _resolve_container_types(container_type, types: list):
    used_types = set(types)
    none_seen = type(None) in used_types
    used_types.discard(type(None))
    if float in used_types:
        used_types.discard(int)
    if len(used_types) == 0:
        return container_type[None] if none_seen else container_type
    elif len(used_types) == 1:
        ret = next(iter(used_types))
    else:
        ret = typing.Union[tuple(used_types)]
    if none_seen:
        ret = typing.Optional[ret]
    return container_type[ret]


def _resolve_tuple_types(types: list):
    if len(types) == 0:
        return typing.Tuple
    elif len(types) <= 3:
        return typing.Tuple[tuple(types)]
    else:
        args = _resolve_container_types(typing.List, types).__args__
        if len(args) == 1:
            return typing.Tuple[args[0], ...]
        else:
            return typing.Tuple[tuple(types)]


def get_type_annotation(obj):
    obj_type = type(obj)
    if obj_type in _PRIMITIVE_TYPES:
        return obj_type
    elif obj_type in _CONTAINER_TYPES:
        if obj_type == dict:
            key_ann = _resolve_container_types(typing.List, _get_contained_type_annotations(obj.keys()))
            value_ann = _resolve_container_types(typing.List, _get_contained_type_annotations(obj.values()))
            return typing.Dict[key_ann.__args__, value_ann.__args__]
        elif obj_type == tuple:
            return _resolve_tuple_types(_get_contained_type_annotations(obj))
        else:
            return _resolve_container_types(
                _CONTAINER_TYPES[obj_type], _get_contained_type_annotations(obj)
            )
    else:
        return obj_type


def make_annotation_string(ann) -> str:
    if hasattr(ann, '__name__'):
        ret = ann.__name__
    elif hasattr(ann, '_name'):
        ret = ann._name
    else:
        ret = str(ann)

    if hasattr(ann, '__module__'):
        module = ann.__module__
        if module not in ('typing', 'builtins'):
            ret = f'{module}.{ret}'

    if hasattr(ann, '__args__'):
        args_anns = []
        for arg in ann.__args__:
            args_anns.append(make_annotation_string(arg))
        ret = f'{ret}[{", ".join(args_anns)}]'
    return ret
