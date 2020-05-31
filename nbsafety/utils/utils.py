# -*- coding: utf-8 -*-
import logging
from typing import cast, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Union

logger = logging.getLogger(__name__)


def retrieve_namespace_attr_or_sub(obj: 'Any', attr_or_sub: 'Union[str, int]', is_subscript: bool):
    try:
        if is_subscript:
            # TODO: more complete list of things that are checkable
            #  or could cause side effects upon subscripting
            if isinstance(obj, dict) and attr_or_sub not in obj:
                raise KeyError()
            else:
                return obj[attr_or_sub]
        else:
            assert isinstance(attr_or_sub, str)
            if not hasattr(obj, attr_or_sub):
                raise AttributeError()
            else:
                return getattr(obj, attr_or_sub)
    except (KeyError, IndexError, AttributeError):
        raise
    # except AssertionError as e:
    #     print(obj, attr_or_sub, is_subscript)
    #     raise e
    except Exception as e:
        logger.warning('unexpected exception: %s' % e)
        raise e
