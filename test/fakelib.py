# -*- coding: future_annotations -*-

y = 7


class Foo:
    def __init__(self):
        self.x = 7

    def set_x(self, new_x):
        self.x = new_x
        return self
