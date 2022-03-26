# -*- coding: utf-8 -*-
import ast
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Tuple, Union

if TYPE_CHECKING:
    from ipyflow.data_model.namespace import Namespace


logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


# TODO: add generate return type to signature
def match_container_obj_or_namespace_with_literal_nodes(
    container_obj_or_namespace: Union[
        "Namespace", Dict[Any, Any], List[Any], Tuple[Any, ...]
    ],
    literal_node: Union[ast.Dict, ast.List, ast.Tuple],
):
    try:
        gen = container_obj_or_namespace.items()  # type: ignore
        assert isinstance(literal_node, ast.Dict), "got %s" % ast.dump(literal_node)
        yield from zip(gen, zip(literal_node.keys, literal_node.values))
    except (AttributeError, TypeError):
        assert isinstance(literal_node, (ast.List, ast.Tuple))
        elts = literal_node.elts
        cur_node = None
        cur_elt_idx = -1
        for i, obj_or_sym in enumerate(container_obj_or_namespace):
            if (
                not isinstance(cur_node, ast.Starred)
                or len(elts) - cur_elt_idx - 1 >= len(container_obj_or_namespace) - i
            ):
                cur_elt_idx += 1
                cur_node = elts[cur_elt_idx]
            yield (i, obj_or_sym), (None, cur_node)
