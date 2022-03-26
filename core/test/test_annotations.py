# -*- coding: utf-8 -*-
from ipyflow.data_model.annotation_utils import (
    get_type_annotation,
    make_annotation_string,
)


def _make_annotation_str_for_obj(obj):
    return make_annotation_string(get_type_annotation(obj))


def test_dict():
    empty = _make_annotation_str_for_obj({})
    assert empty == "Dict", "got %s" % empty
    assert _make_annotation_str_for_obj({1: 2}) == "Dict[int, int]"
    assert _make_annotation_str_for_obj({1: 2, 2: 3.0}) == "Dict[int, float]"
    ann_str = _make_annotation_str_for_obj({1: 2, 2: 3.0, "foo": "bar"})
    assert ann_str == "Dict[Union[int, str], Union[float, str]]", "got %s" % ann_str
    assert (
        _make_annotation_str_for_obj({1: 2, 2: 3.0, 3: None})
        == "Dict[int, Optional[float]]"
    )


def test_list():
    assert _make_annotation_str_for_obj([]) == "List"
    assert _make_annotation_str_for_obj([3, 6.0, 9]) == "List[float]"
    assert _make_annotation_str_for_obj([3, 9]) == "List[int]"
    ann_str = _make_annotation_str_for_obj([3, None])
    assert ann_str == "List[Optional[int]]", "got %s" % ann_str
    assert _make_annotation_str_for_obj([3, None, 42.0]) == "List[Optional[float]]"
    ann_str = _make_annotation_str_for_obj([3, None, 42.0, "foo"])
    assert ann_str == "List[Union[None, float, str]]", "got %s" % ann_str


def test_set():
    assert _make_annotation_str_for_obj(set()) == "Set"
    assert _make_annotation_str_for_obj({3, 6.0, 9}) == "Set[float]"
    assert _make_annotation_str_for_obj({3, 9}) == "Set[int]"
    assert _make_annotation_str_for_obj({3, None}) == "Set[Optional[int]]"
    assert _make_annotation_str_for_obj({3, None, 42.0}) == "Set[Optional[float]]"
    assert (
        _make_annotation_str_for_obj({3, None, 42.0, "foo"})
        == "Set[Union[None, float, str]]"
    )


def test_tuple():
    assert _make_annotation_str_for_obj(()) == "Tuple"
    assert _make_annotation_str_for_obj((3, 4)) == "Tuple[int, int]"
    assert _make_annotation_str_for_obj((3, 4, 5.0)) == "Tuple[int, int, float]"
    assert _make_annotation_str_for_obj((3, 4, 5, 6)) == "Tuple[int, ...]"
    assert _make_annotation_str_for_obj((3, 4, 5, 6.0)) == "Tuple[float, ...]"


class Foo:
    pass


def test_class():
    ann_str = _make_annotation_str_for_obj(Foo)
    assert ann_str == "Type[{prefix}.Foo]".format(prefix=__name__), "got %s" % ann_str
    assert _make_annotation_str_for_obj(
        (Foo, Foo())
    ) == "Tuple[Type[{prefix}.Foo], {prefix}.Foo]".format(prefix=__name__)
    assert _make_annotation_str_for_obj(
        [Foo, Foo()]
    ) == "List[Union[Type[{prefix}.Foo], {prefix}.Foo]]".format(prefix=__name__)
