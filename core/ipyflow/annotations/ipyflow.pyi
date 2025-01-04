# -*- coding: utf-8 -*-
from ipyflow.annotations import NoopCallHandler, module

@module("ipyflow.tracing.uninstrument", "ipyflow")
def uninstrument() -> NoopCallHandler: ...

#
@module("ipyflow.api.lift", "ipyflow")
def mutate() -> NoopCallHandler: ...
