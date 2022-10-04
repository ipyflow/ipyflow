# -*- coding: utf-8 -*-

y = 7


class Foo:
    def __init__(self):
        self.x = 7

    def set_x(self, new_x):
        self.x = new_x
        return self


class OnlyPresentSoThatHandlersCanBeRegistered:
    def method_for_method_stub_presence(self):
        pass

    def method_a(self):
        pass

    def method_b(self):
        pass


def function_for_function_stub_presence():
    pass


def fun_for_testing_kwarg(foo, bar):
    pass
