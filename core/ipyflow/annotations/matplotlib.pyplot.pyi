# -*- coding: utf-8 -*-
from ipyflow.annotations import Mutated, NoopCallHandler, __module__, module

@module("matplotlib.pyplot", "pylab", "d2l.torch")
def show() -> NoopCallHandler: ...

#
@module("matplotlib.pyplot", "pylab", "d2l.torch")
def plot() -> NoopCallHandler: ...

#
@module("matplotlib.pyplot", "pylab")
def figure() -> Mutated[__module__]: ...