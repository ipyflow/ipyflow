import sys
from io import StringIO
from typing import Any, Optional, TextIO

import pyccolo as pyc
from IPython.core.displayhook import DisplayHook
from IPython.core.displaypub import CapturingDisplayPublisher, DisplayPublisher
from IPython.core.interactiveshell import InteractiveShell
from IPython.utils.capture import CapturedIO

from ipyflow.singletons import shell


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


class TeeCompatibleCapturingDisplayPublisher(CapturingDisplayPublisher):
    def clear_output(self, wait=False):
        self.outputs.clear()


class IPyflowCapturedIO(CapturedIO):
    def __init__(self, stdout, stderr, outputs=None, exec_ctr=None) -> None:
        super().__init__(stdout, stderr, outputs=outputs)
        self._exec_ctr = exec_ctr

    def show(self, render_out_expr: bool = True) -> None:
        super().show()
        if not render_out_expr:
            return
        shell_ = shell()
        expr_result = shell_.user_ns.get("Out", {}).get(self._exec_ctr)
        if expr_result is not None:
            shell_.displayhook(expr_result)


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
        self.save_display_pub: Optional[DisplayPublisher] = None
        self._in_context = False

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
            capture_display_pub = TeeCompatibleCapturingDisplayPublisher()
            outputs = capture_display_pub.outputs
            self.shell.display_pub = TeeDisplayPublisher(
                self.shell.display_pub, capture_display_pub
            )

        self._in_context = True
        return IPyflowCapturedIO(
            stdout,
            stderr,
            outputs,
            None if self.shell is None else self.shell.execution_count,
        )

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if not self._in_context:
            return
        self._in_context = False
        if self.sys_stdout is not None:
            sys.stdout = self.sys_stdout
            self.sys_stdout = None
        if self.sys_stderr is not None:
            sys.stderr = self.sys_stderr
            self.sys_stderr = None
        if self.display and self.shell:
            self.shell.display_pub = self.save_display_pub


class OutputRecorder(pyc.BaseTracer):
    should_patch_meta_path = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with self.persistent_fields():
            self.capture_output_tee = CaptureOutputTee()
        self.capture_output = None
        self.capturing_output = False

    @pyc.register_raw_handler(pyc.init_module)
    def init_module(self, *_, **__):
        self.capturing_output = True
        self.capture_output = self.capture_output_tee.__enter__()

    def done_capturing_output(self) -> None:
        if not self.capturing_output:
            return
        self.capturing_output = False
        self.capture_output_tee.__exit__(None, None, None)
