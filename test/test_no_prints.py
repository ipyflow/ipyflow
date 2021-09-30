# -*- coding: future_annotations -*-
"""
The idea of this file is to make sure that no debugging "print" statements
make it into production.
"""
import ast
import os

import nbsafety


_EXCEPTED_FILES = {
    f'./{nbsafety.__name__}/_version.py',
    f'./{nbsafety.__name__}/kernel/install.py'
}


class ContainsPrintVisitor(ast.NodeVisitor):
    def __init__(self):
        self._found_print_call = False

    def __call__(self, filename: str) -> bool:
        with open(filename, 'r') as f:
            self.visit(ast.parse(f.read()))
        ret = self._found_print_call
        self._found_print_call = False
        return ret

    def visit_Call(self, node: ast.Call):
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == 'print':
            self._found_print_call = True


def test_no_prints():
    contains_print = ContainsPrintVisitor()
    for root, _, files in os.walk(os.path.join(os.curdir, nbsafety.__name__)):
        for filename in files:
            if not filename.endswith('.py') or filename in _EXCEPTED_FILES:
                continue
            filename = os.path.join(root, filename)
            if filename in _EXCEPTED_FILES:
                continue
            assert not contains_print(filename), f'file {filename} had a print statement!'
