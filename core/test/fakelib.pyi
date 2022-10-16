# -*- coding: utf-8 -*-
from ipyflow.annotations import Mutate, __module__, handler_for, module, self

foo = bar = baz = None

class OnlyPresentSoThatHandlersCanBeRegistered:
    def method_for_method_stub_presence(self) -> Mutate[self]: ...
    #
    @handler_for("method_a", "method_b")
    def handler_by_a_different_name(self) -> Mutate[self]: ...

def function_for_function_stub_presence() -> Mutate[__module__]: ...

#
def fun_for_testing_kwarg(foo, bar) -> Mutate[bar]: ...

#
def fun_for_testing_kwonlyarg(foo, *, bar) -> Mutate[bar]: ...

#
def fun_for_testing_mutate_multiple(foo, bar, baz) -> Mutate[foo, baz]: ...

#
""":sys.version_info >= (3, 8)
def fun_for_testing_posonlyarg(foo, /, bar) -> Mutate[foo]: ...
"""

#
@module("non_fakelib_module")
def function_in_another_module() -> Mutate[__module__]: ...

#
@module("non_fakelib_module")
class ClassInAnotherModule:
    def method_in_another_module(self) -> Mutate[self]: ...
