# -*- coding: utf-8 -*-


class KeyDict(dict):
    def __missing__(self, key):
        return key


def cleanup_discard(d, key, val):
    s = d.get(key, set())
    s.discard(val)
    if len(s) == 0:
        d.pop(key, None)


def cleanup_pop(d, key, val):
    d2 = d.get(key, {})
    d2.pop(val, None)
    if len(d2) == 0:
        d.pop(key, None)
