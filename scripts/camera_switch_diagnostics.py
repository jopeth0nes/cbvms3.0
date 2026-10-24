"""Small, import-safe helpers for camera-switch diagnostic artifacts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time


def write_failure_artifact(folder: Path, phase: str, exc: BaseException) -> Path:
    """Record that a phase failed without persisting exception text or secrets."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "failure.json"
    payload = {"phase": phase, "error_kind": type(exc).__name__,
               "monotonic": time.monotonic()}
    fd, tmp_name = tempfile.mkstemp(prefix=".failure-", dir=folder)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream)
            stream.write("\n")
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return target


def valid_result(result, camera_generation: int, monitor_generation: int,
                 frame_token=None) -> bool:
    """Whether a result is both current and accepted by the task's validity gate."""
    if result is None:
        return False
    task = getattr(result, "task", None)
    if task is None:
        return False
    context = getattr(task, "context", None)
    return bool(getattr(result, "detail", "") != "Assessment unavailable"
                and task.valid() and context is not None
                and context.generation == monitor_generation
                and getattr(task, "camera_generation", None) == camera_generation
                and (frame_token is None or context.frame_id[0] == frame_token))


def callback_wrapper(record, last_completed, callback):
    """Wrap a Tk callback and report both entry and successful/failed return."""
    name = getattr(callback, "__qualname__", type(callback).__name__)

    def invoke(*args, **kwargs):
        started = time.monotonic()
        record("tk_callback_enter", callback=name)
        completed = False
        try:
            value = callback(*args, **kwargs)
            completed = True
            return value
        finally:
            if completed:
                last_completed[0] = name
            record("tk_callback_return", callback=name,
                   completed=completed, ms=round((time.monotonic() - started) * 1000, 3))
    return invoke


def trace_tk_callbacks(record, last_completed):
    """Trace scheduled, widget and binding callbacks, including constructor jobs."""
    import tkinter
    from unittest.mock import patch
    original = tkinter.CallWrapper.__call__

    def invoke(wrapper, *args):
        name = getattr(wrapper.func, "__name__", type(wrapper.func).__name__)
        started = time.monotonic()
        record("tk_callback_enter", callback=name)
        try:
            return original(wrapper, *args)
        finally:
            # CallWrapper reports exceptions separately through the root handler.
            last_completed[0] = name
            record("tk_callback_return", callback=name,
                   ms=round((time.monotonic() - started) * 1000, 3))
    return patch.object(tkinter.CallWrapper, "__call__", invoke)
