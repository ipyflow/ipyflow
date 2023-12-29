# -*- coding: utf-8 -*-
import ast
import logging
import sys
from contextlib import contextmanager
from io import StringIO
from typing import Any, Callable, Generator, List, Optional, TextIO

from IPython.core.displayhook import DisplayHook
from IPython.core.displaypub import CapturingDisplayPublisher, DisplayPublisher
from IPython.core.interactiveshell import ExecutionResult, InteractiveShell
from IPython.utils.capture import CapturedIO
from traitlets import MetaHasTraits

from ipyflow.singletons import shell

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class _IpythonState:
    def __init__(self) -> None:
        self.cell_counter: Optional[int] = None

    @contextmanager
    def save_number_of_currently_executing_cell(self) -> Generator[None, None, None]:
        self.cell_counter = shell().execution_count
        try:
            yield
        finally:
            self.cell_counter = None

    @contextmanager
    def ast_transformer_context(
        self, transformers: List[ast.NodeTransformer]
    ) -> Generator[None, None, None]:
        old = shell().ast_transformers
        shell().ast_transformers = old + transformers
        try:
            yield
        finally:
            shell().ast_transformers = old

    @contextmanager
    def input_transformer_context(
        self, transformers: List[Callable[[List[str]], List[str]]]
    ) -> Generator[None, None, None]:
        old = shell().input_transformers_post
        shell().input_transformers_post = old + transformers
        try:
            yield
        finally:
            shell().input_transformers_post = old


_IPY = _IpythonState()


@contextmanager
def save_number_of_currently_executing_cell() -> Generator[None, None, None]:
    with _IPY.save_number_of_currently_executing_cell():
        yield


@contextmanager
def ast_transformer_context(transformers) -> Generator[None, None, None]:
    with _IPY.ast_transformer_context(transformers):
        yield


@contextmanager
def input_transformer_context(transformers) -> Generator[None, None, None]:
    with _IPY.input_transformer_context(transformers):
        yield


def cell_counter() -> int:
    if _IPY.cell_counter is None:
        raise ValueError("should be inside context manager here")
    return _IPY.cell_counter


def run_cell(cell, **kwargs) -> ExecutionResult:
    return shell().run_cell(
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
    def __init__(self, disp1: DisplayHook, disp2: DisplayHook) -> None:
        self.disp1 = disp1
        self.disp2 = disp2

    def __getattr__(self, item: str) -> Any:
        if item in ("disp1", "disp2"):
            raise AttributeError()
        # delegate to the first display hook
        return getattr(self.disp1, item)

    def __call__(self, result=None) -> None:
        self.disp1(result=result)
        self.disp2(result=result)


class TeeDisplayPublisher:
    def __init__(self, pub1: DisplayPublisher, pub2: DisplayPublisher) -> None:
        self.pub1 = pub1
        self.pub2 = pub2

    def __getattr__(self, item: str) -> Any:
        if item in ("pub1", "pub2"):
            raise AttributeError()
        # delegate to the first publisher
        return getattr(self.pub1, item)

    def publish(self, *args, **kwargs) -> None:
        self.pub1.publish(*args, **kwargs)
        self.pub2.publish(*args, **kwargs)

    def clear_output(self, *args, **kwargs) -> None:
        self.pub1.clear_output(*args, **kwargs)
        self.pub2.clear_output(*args, **kwargs)

    def set_parent(self, *args, **kwargs) -> None:
        if hasattr(self.pub1, "set_parent"):
            self.pub1.set_parent(*args, **kwargs)
        if hasattr(self.pub2, "set_parent"):
            self.pub2.set_parent(*args, **kwargs)


class CaptureOutputTee:
    """
    Context manager for capturing and replicating stdout/err and rich display publishers.
    NB: This is a modified version of IPython.utils.capture.capture_output that doesn't capture
      the displayhook as well (the thing that renders the final expression in a cell). Trying
      to capture both it and the display publisher seems like it can confuse ipywidgets, and
      for this we can always rerender it anyway if necessary using the Out dictionary.
    """

    def __init__(self, stdout=True, stderr=True, display=True) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.display = display
        self.shell: Optional[InteractiveShell] = None
        self.sys_stdout: Optional[TextIO] = None
        self.sys_stderr: Optional[TextIO] = None

    def __enter__(self) -> CapturedIO:
        self.sys_stdout = sys.stdout
        self.sys_stderr = sys.stderr

        if self.display:
            self.shell = shell()
            if self.shell is None:
                self.save_display_pub = None
                self.display = False

        stdout = stderr = outputs = None
        if self.stdout:
            stdout = StringIO()
            sys.stdout = Tee(sys.stdout, stdout)  # type: ignore
        if self.stderr:
            stderr = StringIO()
            sys.stderr = Tee(sys.stderr, stderr)  # type: ignore
        if self.display and self.shell is not None:
            self.save_display_pub = self.shell.display_pub
            capture_display_pub = CapturingDisplayPublisher()
            outputs = capture_display_pub.outputs
            self.shell.display_pub = TeeDisplayPublisher(
                self.save_display_pub, capture_display_pub
            )

        return CapturedIO(stdout, stderr, outputs)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        sys.stdout = self.sys_stdout
        sys.stderr = self.sys_stderr
        if self.display and self.shell:
            self.shell.display_pub = self.save_display_pub


_PURPLE = "\033[95m"
_RED = "\033[91m"
_RESET = "\033[0m"


# allow exceptions for the test_no_prints test
print_ = print


def print_purple(text: str, **kwargs) -> None:
    # The ANSI escape code for purple text is \033[95m
    # The \033 is the escape code, and [95m specifies the color (purple)
    # Reset code is \033[0m that resets the style to default
    print_(f"{_PURPLE}{text}{_RESET}", **kwargs)


def print_red(text: str, **kwargs) -> None:
    print_(f"{_RED}{text}{_RESET}", **kwargs)


def make_mro_inserter_metaclass(old_class, new_class):
    class MetaMroInserter(MetaHasTraits):
        def mro(cls):
            ret = []
            for clazz in super().mro():
                if clazz is old_class:
                    ret.append(new_class)
                ret.append(clazz)
            return ret

    return MetaMroInserter
