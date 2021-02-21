# -*- coding: future_annotations -*-

class KeyDict(dict):
    def __missing__(self, key):
        return key
