# -*- coding: utf-8 -*-
from typing import Any, Mapping, Sequence


class KeyDict(dict):
    def __missing__(self, key):
        return key
