# -*- coding: utf-8 -*-
import sys

# Remove the CWD from sys.path while we load stuff.
# This is added back by InteractiveShellApp.init_path()
# TODO: probably need to make this separate from ipyflow package so that we can
#  completely avoid imports until after removing cwd from sys.path
if sys.path[0] == "":
    del sys.path[0]

from ipykernel import kernelapp as app

from ipyflow.kernel import IPyflowKernel

app.launch_new_instance(kernel_class=IPyflowKernel)
