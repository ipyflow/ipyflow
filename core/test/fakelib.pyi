# -*- coding: utf-8 -*-
from ipyflow.annotations import Mutated, __module__, self

class OnlyPresentSoThatHandlersCanBeRegistered:
    def method_for_method_stub_presence(self) -> Mutated[self]: ...

def function_for_function_stub_presence() -> Mutated[__module__]: ...
