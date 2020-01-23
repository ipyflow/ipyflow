#!/usr/bin/env python
# coding: utf-8
import inspect
import os
import sys


# This is meant to be run  in a notebook.
# ref: https://nedyoxall.github.io/tracing_classes_and_methods_called_from_notebook.html
def tracefunc(frame, event, arg):
    """ Function to track the methods, classes and filenames that are called from Jupyter notebook.
        Will need some tweaking for use outside of notebook environment.
        Useful for tracing what functions have been used in complex packages.
        Inspired by: http://stackoverflow.com/questions/8315389/how-do-i-print-functions-as-they-are-called
        
        Inputs: - frame, event, arg -> See the docs: https://docs.python.org/3/library/sys.html#sys.settrace
                - package_name -> string of the package you want to track
                                  (without this get a load of notebook faff printed out too)
        
        Outputs: - prints to screen the filename, class and methods called by running cell.
        
        Usage: Turn on -> sys.settrace(tracefunc)
               Turn off -> sys.settrace(None)
    """
    
    file_name = frame.f_code.co_filename
    func_name = frame.f_code.co_name
    
    # this is a bit of a hack to get the class out of the locals
    # - it relies on 'self' being used... normally a safe assumption!
    try:
        class_name = frame.f_locals['self'].__class__.__name__ 
    except (KeyError, AttributeError):
        class_name = "No Class"
                  
    # notebook filenames appear as 'ipython-input...'
    if 'ipython-input' in file_name:
        # another thing that appears to be specific to notebooks
        # this is where the work gets done!
        if event == "call":
            print(inspect.getsource(frame))
            print(frame.f_lineno)
#             print("Filename: " + os.path.basename(file_name) + \
#                   " -> Class: " + class_name + \
#                   " -> Function: " + func_name)
    return tracefunc


sys.settrace(tracefunc)
