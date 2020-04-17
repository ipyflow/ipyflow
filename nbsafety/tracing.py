# -*- coding: utf-8 -*-


# The trace function we use to capture the frame dict of each scope.
def capture_frame_at_run_time(frame, event, arg):
    original_frame = frame
    if "ipython-input" in frame.f_code.co_filename:
        if event == "call":
            path = ()
            while frame.f_code.co_name != "<module>":
                path = (frame.f_code.co_name,) + path
                frame = frame.f_back
            if path not in capture_frame_at_run_time.dictionary:
                capture_frame_at_run_time.dictionary[path] = original_frame
