# -*- coding: utf-8 -*-
from ipyflow.annotations import module
from ipyflow.tracing.external_call_handler import ModuleMutation, NoopCallHandler

@module("matplotlib.pyplot", "pylab", "d2l.torch")
def show() -> NoopCallHandler: ...

#
@module("matplotlib.pyplot", "pylab", "d2l.torch")
def plot() -> NoopCallHandler: ...

#
@module("matplotlib.pyplot", "pylab")
def figure() -> ModuleMutation: ...
