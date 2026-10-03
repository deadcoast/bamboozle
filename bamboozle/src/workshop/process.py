"""Drain child pipes fairly with fixed memory and bounded cleanup.

Python >=3.13 supports nonblocking pipe descriptors on Windows and POSIX. No
reader threads, output files, or communicate() buffers survive a call. Only the
direct child is owned; descendants may outlive it and hold inherited pipes open.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, BinaryIO

STDOUT_LIMIT = 8000
STDERR_LIMIT = 4000
READ_CHUNK = 64 * 1024
POLL_INTERVAL = 0.01
DRAIN_GRACE = 1.0
STOP_GRACE = 2.0


class StartError(OSError):
    """The child was never started; distinct from pipe errors after spawning."""


class _Tail:
    def __init__(self, limit: int):
        self.limit = limit
        self.data = bytearray()
        self.total = 0
        self.eof = False

    def append(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if len(chunk) >= self.limit:
            self.data[:] = chunk[-self.limit :]
        else:
            excess = len(self.data) + len(chunk) - self.limit
            if excess > 0:
                del self.data[:excess]
            self.data.extend(chunk)


def run_captured(command: list[str], cwd: Path, timeout: float) -> dict[str, Any]:
    """Start once; retain byte tails and reap the direct child when possible.

    Spawn errors propagate. Failures after spawn return started-process evidence.
    Output EOF and process exit are independent; neither can bypass the deadline.
    """
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError as exc:
        raise StartError(str(exc)) from exc
    tails = {"stdout": _Tail(STDOUT_LIMIT), "stderr": _Tail(STDERR_LIMIT)}
    streams: dict[str, BinaryIO] = {}
    deadline = time.monotonic() + timeout
    exit_deadline: float | None = None
    stop_deadline: float | None = None
    timed_out = False
    capture_errors: list[str] = []
    termination_error: str | None = None

    def stop() -> None:
        nonlocal stop_deadline, termination_error
        if stop_deadline is not None:
            return
        stop_deadline = time.monotonic() + STOP_GRACE
        if process.poll() is None:
            try:
                process.kill()
            except OSError as exc:
                termination_error = str(exc)

    try:
        for name in tails:
            stream = getattr(process, name)
            if stream is None:
                raise RuntimeError("Subprocess pipe was not created")
            try:
                os.set_blocking(stream.fileno(), False)
            except (OSError, ValueError, NotImplementedError) as exc:
                capture_errors.append(f"{name} setup: {exc}")
                stop()
            else:
                streams[name] = stream

        while True:
            now = time.monotonic()
            code = process.poll()
            if code is not None and exit_deadline is None:
                exit_deadline = now + DRAIN_GRACE
            if code is None and now >= deadline and stop_deadline is None:
                timed_out = True
                stop()

            progress = False
            # One bounded read per stream per iteration prevents a noisy stream
            # from starving the other stream or the timeout/cleanup checks.
            for name, stream in list(streams.items()):
                try:
                    chunk = os.read(stream.fileno(), READ_CHUNK)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError as exc:
                    capture_errors.append(f"{name} read: {exc}")
                    del streams[name]
                    stop()
                    continue
                if chunk:
                    tails[name].append(chunk)
                    progress = True
                else:
                    tails[name].eof = True
                    del streams[name]

            if process.poll() is not None and not streams:
                break
            boundaries = [
                value for value in (exit_deadline, stop_deadline) if value is not None
            ]
            if boundaries and time.monotonic() >= min(boundaries):
                break
            if not progress:
                time.sleep(POLL_INTERVAL)
    except BaseException:
        # Cancellation/interrupts must not strand the direct child. Do not wait
        # for inherited pipes or try to manage descendants during cancellation.
        stop()
        try:
            process.wait(timeout=STOP_GRACE)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError as exc:
                    capture_errors.append(f"pipe close: {exc}")

    code = process.poll()
    complete = all(tail.eof for tail in tails.values()) and not capture_errors
    if timed_out:
        status, result_code = "timed_out", 124
    elif capture_errors or code is None:
        status, result_code = "capture_failed", 125
    elif code != 0:
        status, result_code = "failed", code
    elif not complete:
        status, result_code = "capture_incomplete", 125
    else:
        status, result_code = "completed", code
    result: dict[str, Any] = {
        "status": status,
        "returncode": result_code,
        "child_returncode": code,
        "child_exited": code is not None,
        "pid": process.pid,
        "capture": {
            "mode": "bounded_byte_tails",
            "complete": complete,
            "read_chunk_bytes": READ_CHUNK,
            "drain_grace_seconds": DRAIN_GRACE,
            "stop_grace_seconds": STOP_GRACE,
            "errors": capture_errors,
        },
    }
    for name, tail in tails.items():
        result[name] = tail.data.decode("utf-8", errors="replace")
        result[f"{name}_truncated"] = tail.total > tail.limit
        result["capture"][name] = {
            "limit_bytes": tail.limit,
            "bytes_read": tail.total,
            "bytes_retained": len(tail.data),
            "eof": tail.eof,
        }
    if stop_deadline is not None:
        result["termination_scope"] = "direct_child_only"
    if not complete:
        result["capture"]["incomplete_reason"] = (
            "pipe_error" if capture_errors else "drain_deadline"
        )
    if termination_error is not None:
        result["termination_error"] = termination_error
    return result
