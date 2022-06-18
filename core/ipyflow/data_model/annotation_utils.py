# -*- coding: utf-8 -*-
import typing
from typing import Any, Iterable, List

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
            key_ann = _resolve_container_types(
                typing.List, _get_contained_type_annotations(obj.keys())
            )
            value_ann = _resolve_container_types(
                typing.List, _get_contained_type_annotations(obj.values())
            )
            key_args = getattr(key_ann, "__args__", None)
            value_args = getattr(value_ann, "__args__", None)
            if key_args is None or value_args is None:
                return typing.Dict
            else:
                if len(key_args) == 1:
                    key_args = key_args[0]
                if len(value_args) == 1:
                    value_args = value_args[0]
                return typing.Dict[key_args, value_args]
        elif obj_type == tuple:
            return _resolve_tuple_types(_get_contained_type_annotations(obj))
        else:
            return _resolve_container_types(
                _CONTAINER_TYPES[obj_type], _get_contained_type_annotations(obj)
            )
    elif obj_type is type:
        return typing.Type[obj]
    else:
        return obj_type


def make_annotation_string(ann) -> str:
    if ann is type(None):
        ret = "None"
    elif hasattr(ann, "__name__"):
        ret = ann.__name__
    elif hasattr(ann, "_name"):
        ret = ann._name
        if ret is None:
            args = ann.__args__
            if args[-1] is type(None) and len(args) == 2:
                ret = "Optional"
            else:
                ret = "Union"
    elif ann is ...:
        ret = "..."
    else:
        ret = str(ann)

    if ret.startswith("typing.") and "[" in ret:
        ret = ret.split(".")[1].split("[")[0]

    module = getattr(ann, "__module__", None)
    if module is not None and module not in ("typing", "builtins", "__main__"):
        ret = f"{module}.{ret}"

    ann_args = getattr(ann, "__args__", None)
    if ann_args is not None:
        ann_args = [arg for arg in ann_args if not isinstance(arg, typing.TypeVar)]
        if (
            ret in ("Optional", "Union")
            and len(ann_args) > 0
            and ann_args[-1] is type(None)
        ):
            if len(ann_args) == 2:
                ann_args = ann_args[:-1]
            if len(ann_args) == 1:
                ret = "Optional"
        if len(ann_args) > 0:
            args_anns = []
            for arg in ann_args:
                args_anns.append(make_annotation_string(arg))
            should_sort = ret == "Union"
            ret = f'{ret}[{", ".join(sorted(args_anns) if should_sort else args_anns)}]'
    return ret
