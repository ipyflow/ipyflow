# -*- coding: utf-8 -*-
from ipyflow.annotations import Mutated, __module__, module
from ipyflow.tracing.external_call_handler import NoopCallHandler

@module("matplotlib.pyplot", "pylab", "d2l.torch")
def show() -> NoopCallHandler: ...

#
@module("matplotlib.pyplot", "pylab", "d2l.torch")
def plot() -> NoopCallHandler: ...

#
@module("matplotlib.pyplot", "pylab")
def figure() -> Mutated[__module__]: ...
