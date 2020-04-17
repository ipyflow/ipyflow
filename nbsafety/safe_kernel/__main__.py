# -*- coding: utf-8 -*-
from ipykernel.kernelapp import IPKernelApp
from .kernel import SafeKernel
IPKernelApp.launch_instance(kernel_class=SafeKernel)
