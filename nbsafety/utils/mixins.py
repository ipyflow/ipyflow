# -*- coding: future_annotations -*-
# import gc
# import weakref
# import sys


class CommonEqualityMixin:
    def __eq__(self, other):
        return isinstance(other, self.__class__) and (self.__dict__ == other.__dict__)

    def __ne__(self, other):
        return not (self == other)


# class EnforceSingletonMixin:
#     _INSTANCE = None
#
#     def __init__(self):
#         if self.__class__._INSTANCE is not None and self.__class__._INSTANCE() is not None:
#             gc.collect()
#             print(sys.getrefcount(self.__class__._INSTANCE()))
#             print(self.__class__._INSTANCE())
#             print(gc.get_referrers(self.__class__._INSTANCE()))
#             assert False
#         self.__class__._INSTANCE = weakref.ref(self)
#
#     def __del__(self):
#         self.__class__._INSTANCE = None
