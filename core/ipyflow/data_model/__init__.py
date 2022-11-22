# -*- coding: utf-8 -*-
from typing import Tuple

DUPED_ATTRSUB_CLASSES: Tuple[Tuple[str, str], ...] = (
    ("pandas", "DataFrame"),
    ("modin.pandas", "DataFrame"),
)
