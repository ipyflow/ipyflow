# -*- coding: utf-8 -*-
import ast
import logging
import sys
from contextlib import contextmanager
from io import StringIO
from typing import Callable, List, Optional

from IPython import get_ipython
from IPython.utils.capture import CapturedIO


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


def _ipython():
    return get_ipython()


class _IpythonState:
    def __init__(self):
        self.cell_counter: Optional[int] = None

    @contextmanager
    def save_number_of_currently_executing_cell(self):
        self.cell_counter = _ipython().execution_count
        yield
        self.cell_counter = None

    @contextmanager
    def ast_transformer_context(self, transformers: List[ast.NodeTransformer]):
        old = _ipython().ast_transformers
        _ipython().ast_transformers = old + transformers
        yield
        _ipython().ast_transformers = old

    @contextmanager
    def input_transformer_context(
        self, transformers: List[Callable[[List[str]], List[str]]]
    ):
        old = _ipython().input_transformers_post
        _ipython().input_transformers_post = old + transformers
        yield
        _ipython().input_transformers_post = old


_IPY = _IpythonState()


def save_number_of_currently_executing_cell():
    return _IPY.save_number_of_currently_executing_cell()


def ast_transformer_context(transformers):
    return _IPY.ast_transformer_context(transformers)


def input_transformer_context(transformers):
    return _IPY.input_transformer_context(transformers)


def cell_counter() -> int:
    if _IPY.cell_counter is None:
        raise ValueError("should be inside context manager here")
    return _IPY.cell_counter


def run_cell(cell, **kwargs):
    return _ipython().run_cell(
        cell,
        store_history=kwargs.pop("store_history", True),
        silent=kwargs.pop("silent", False),
    )


class Tee:
    def __init__(self, out1, out2):
        self.out1 = out1
        self.out2 = out2

    def __getattr__(self, item):
        if item in ("out1", "out2"):
            raise AttributeError()
        # delegate to the first output stream
        return getattr(self.out1, item)

    def write(self, data):
        self.out1.write(data)
        self.out2.write(data)

    def flush(self):
        self.out1.flush()
        self.out2.flush()


class TeeDisplayHook:
    def __init__(self, disp1, disp2):
        self.disp1 = disp1
        self.disp2 = disp2

    def __getattr__(self, item):
        if item in ("disp1", "disp2"):
            raise AttributeError()
        # delegate to the first display hook
        return getattr(self.disp1, item)

    def __call__(self, result=None):
        self.disp1(result=result)
        self.disp2(result=result)


class TeeDisplayPublisher:
    def __init__(self, pub1, pub2):
        self.pub1 = pub1
        self.pub2 = pub2

    def __getattr__(self, item):
        if item in ("pub1", "pub2"):
            raise AttributeError()
        # delegate to the first publisher
        return getattr(self.pub1, item)

    def publish(self, *args, **kwargs):
        self.pub1.publish(*args, **kwargs)
        self.pub2.publish(*args, **kwargs)

    def clear_output(self, *args, **kwargs):
        self.pub1.clear_output(*args, **kwargs)
        self.pub2.clear_output(*args, **kwargs)

    def set_parent(self, *args, **kwargs):
        self.pub1.set_parent(*args, **kwargs)
        self.pub2.set_parent(*args, **kwargs)


class capture_output_tee:
    """context manager for capturing and replicating stdout/err"""

    def __init__(self, stdout=True, stderr=True, display=True):
        self.stdout = stdout
        self.stderr = stderr
        self.display = display
        self.shell = None

    def __enter__(self):
        from IPython.core.getipython import get_ipython
        from IPython.core.displaypub import CapturingDisplayPublisher
        from IPython.core.displayhook import CapturingDisplayHook

        self.sys_stdout = sys.stdout
        self.sys_stderr = sys.stderr

        if self.display:
            self.shell = get_ipython()
            if self.shell is None:
                self.save_display_pub = None
                self.display = False

        stdout = stderr = None
        capture_display_pub = None
        if self.stdout:
            stdout = StringIO()
            sys.stdout = Tee(sys.stdout, stdout)
        if self.stderr:
            stderr = StringIO()
            sys.stderr = Tee(sys.stderr, stderr)
        if self.display:
            self.save_display_pub = self.shell.display_pub
            capture_display_pub = CapturingDisplayPublisher()
            self.shell.display_pub = TeeDisplayPublisher(
                self.save_display_pub, capture_display_pub
            )
            self.save_display_hook = sys.displayhook
            capture_display_hook = CapturingDisplayHook(
                shell=self.shell, outputs=capture_display_pub.outputs
            )
            sys.displayhook = TeeDisplayHook(
                sys.displayhook,
                capture_display_hook,
            )

        return CapturedIO(
            stdout,
            stderr,
            None if capture_display_pub is None else capture_display_pub.outputs,
        )

    def __exit__(self, exc_type, exc_value, traceback):
        sys.stdout = self.sys_stdout
        sys.stderr = self.sys_stderr
        if self.display and self.shell:
            self.shell.display_pub = self.save_display_pub
            sys.displayhook = self.save_display_hook
