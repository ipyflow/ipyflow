# -*- coding: utf-8 -*-
from ipyflow.annotations import NoopCallHandler, module

@module("ipyflow.tracing.uninstrument", "ipyflow")
def uninstrument() -> NoopCallHandler: ...
