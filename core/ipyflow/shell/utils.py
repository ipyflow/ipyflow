# -*- coding: utf-8 -*-
from traitlets import MetaHasTraits


def make_mro_inserter_metaclass(old_class, new_class):
    class MetaMroInserter(MetaHasTraits):
        def mro(cls):
            ret = []
            for clazz in super().mro():
                if clazz is old_class:
                    ret.append(new_class)
                ret.append(clazz)
            return ret

    return MetaMroInserter
