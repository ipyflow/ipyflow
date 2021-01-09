# -*- coding: utf-8 -*-

class KeyDict(dict):
    def __missing__(self, key):
        return key
