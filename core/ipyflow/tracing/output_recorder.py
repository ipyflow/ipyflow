import sys
import threading
from io import StringIO, TextIOBase
from typing import Any, Optional, Union

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


class StdstreamProxy:
    def __init__(self, capture_output_tee: "CaptureOutputTee", name: str) -> None:
        self._capture_output_tee = capture_output_tee
        self._name = name
        self._inited = True

    def _stream(self, include_tee: bool = True) -> Union[TextIOBase, Tee]:
        stream = None
        if include_tee and threading.current_thread().name == "MainThread":
            stream = getattr(self._capture_output_tee, f"tee_sys_{self._name}")
        if stream is None:
            stream = getattr(self._capture_output_tee, f"sys_{self._name}")
        return stream

    def __getattr__(self, item: str) -> object:
        return getattr(self._stream(), item)

    def __setattr__(self, key: str, value: object) -> None:
        if not self.__dict__.get("_inited", False):
            super().__setattr__(key, value)
        else:
            self._stream(include_tee=False).__setattr__(key, value)


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
        self.sys_stdout = sys.stdout
        self.sys_stderr = sys.stderr
        self.tee_sys_stdout: Optional[Tee] = None
        self.tee_sys_stderr: Optional[Tee] = None
        self.save_display_pub: Optional[DisplayPublisher] = None
        self._in_context = False

    def __enter__(self) -> CapturedIO:
        if self.display:
            self.shell = shell()
            if self.shell is None:
                self.save_display_pub = None
                self.display = False

        stdout = stderr = outputs = None
        if self.stdout:
            stdout = StringIO()
            stdout_tee = Tee(self.sys_stdout, stdout)
            self.tee_sys_stdout = stdout_tee
            # sys.stdout = stdout_tee  # type: ignore
        if self.stderr:
            stderr = StringIO()
            stderr_tee = Tee(self.sys_stderr, stderr)
            self.tee_sys_stderr = stderr_tee
            # sys.stderr = stderr_tee  # type: ignore
        if self.display and self.shell is not None:
            self.save_display_pub = self.shell.display_pub
            capture_display_pub = TeeCompatibleCapturingDisplayPublisher()
            outputs = capture_display_pub.outputs
            tee_display_pub = TeeDisplayPublisher(
                self.shell.display_pub, capture_display_pub
            )
            self.shell.display_pub = tee_display_pub

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
        if self.stdout:
            # sys.stdout = self.sys_stdout
            self.tee_sys_stdout = None
        if self.stderr:
            # sys.stderr = self.sys_stderr
            self.tee_sys_stderr = None
        if self.display and self.shell:
            self.shell.display_pub = self.save_display_pub


class OutputRecorder(pyc.BaseTracer):
    should_patch_meta_path = False
    capture_output_tee = CaptureOutputTee()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.capture_output = None
        self.capturing_output = False

    @pyc.register_raw_handler(pyc.init_module)
    def init_module(self, *_, **__):
        if not isinstance(sys.stdout, StdstreamProxy):
            self.capture_output_tee.sys_stdout = sys.stdout
            self.capture_output_tee.sys_stderr = sys.stderr
            sys.stdout = StdstreamProxy(self.capture_output_tee, "stdout")
            sys.stderr = StdstreamProxy(self.capture_output_tee, "stderr")
        self.capturing_output = True
        self.capture_output = self.capture_output_tee.__enter__()

    def done_capturing_output(self) -> None:
        if not self.capturing_output:
            return
        self.capturing_output = False
        self.capture_output_tee.__exit__(None, None, None)
