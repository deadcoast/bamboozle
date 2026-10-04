"""Bounded Python check execution with owned process-group/job cleanup.

The trusted bootstrap waits for containment before loading project code. This
controls process lifetime, not filesystem/network permissions or hostile code.
Windows job semantics: https://learn.microsoft.com/windows/win32/procthread/job-objects
"""

from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

BOOTSTRAP = """
import json, os, runpy, sys
if sys.stdin.buffer.read(1) != b'1':
    raise SystemExit(125)
sys.stdin.close()
sys.stdin = open(os.devnull, 'r')
sys.dont_write_bytecode = True
paths, mode, target, *args = sys.argv[1:]
sys.path[:0] = [os.path.join(os.getcwd(), path) for path in json.loads(paths)]
sys.argv = [target, *args]
if mode == 'module':
    runpy.run_module(target, run_name='__main__', alter_sys=True)
else:
    runpy.run_path(target, run_name='__main__')
"""


class _Limits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _Extended(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _Limits),
        ("IoInfo", ctypes.c_uint64 * 6),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("times", ctypes.c_int64 * 4),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


class _Job:
    def __init__(self) -> None:
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        declarations = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
                wintypes.BOOL,
            ),
            "QueryInformationJobObject": (
                [
                    wintypes.HANDLE,
                    ctypes.c_int,
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    ctypes.c_void_p,
                ],
                wintypes.BOOL,
            ),
            "OpenProcess": (
                [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD],
                wintypes.HANDLE,
            ),
            "AssignProcessToJobObject": (
                [wintypes.HANDLE, wintypes.HANDLE],
                wintypes.BOOL,
            ),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, result) in declarations.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, pid: int) -> None:
        process = self.api.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(process)

    def stop(self) -> bool:
        if not self.api.TerminateJobObject(self.handle, 125):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + 2
        while True:
            accounting = _Accounting()
            if not self.api.QueryInformationJobObject(
                self.handle,
                1,
                ctypes.byref(accounting),
                ctypes.sizeof(accounting),
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if accounting.ActiveProcesses == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def run_python(
    arguments: list[str],
    cwd: Path,
    timeout: int,
    output_bytes: int,
    python_paths: list[str],
) -> dict[str, Any]:
    """Run configured arguments once. Retain bounded tails; always clean up ownership."""
    module = arguments[0] == "-m"
    target = arguments[1] if module else arguments[0]
    rest = arguments[2:] if module else arguments[1:]
    environment = {
        key: os.environ[key]
        for key in (
            "SystemRoot",
            "WINDIR",
            "COMSPEC",
            "PATH",
            "PATHEXT",
            "LANG",
            "LC_ALL",
        )
        if key in os.environ
    }
    scratch = cwd.parent / "scratch"
    scratch.mkdir()
    environment.update(
        {
            key: str(scratch)
            for key in (
                "TEMP",
                "TMP",
                "TMPDIR",
                "HOME",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
            )
        }
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    job = _Job() if os.name == "nt" else None
    process: subprocess.Popen[bytes] | None = None
    tails = {"stdout": bytearray(), "stderr": bytearray()}
    totals = {name: 0 for name in tails}
    streams: dict[str, Any] = {}
    timed_out, complete, cleaned, started = False, False, False, False
    error = None
    code = None
    begin = time.monotonic()
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-u",
                "-X",
                "utf8",
                "-c",
                BOOTSTRAP,
                json.dumps(python_paths),
                "module" if module else "script",
                target,
                *rest,
            ],
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if job:
            job.assign(process.pid)
        for name in tails:
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            streams[name] = stream
        assert process.stdin is not None
        process.stdin.write(b"1")
        process.stdin.close()
        started = True
        deadline = begin + timeout
        exit_deadline = None
        while True:
            code = process.poll()
            now = time.monotonic()
            if code is not None and exit_deadline is None:
                exit_deadline = now + 0.25
            if now >= deadline:
                timed_out = code is None
                break
            progress = False
            for name, stream in list(streams.items()):
                try:
                    chunk = os.read(stream.fileno(), 65536)
                except (BlockingIOError, InterruptedError):
                    continue
                if not chunk:
                    del streams[name]
                else:
                    progress = True
                    totals[name] += len(chunk)
                    tails[name].extend(chunk)
                    del tails[name][:-output_bytes]
            if code is not None and not streams:
                complete = True
                break
            if exit_deadline is not None and now >= exit_deadline:
                break
            if not progress:
                time.sleep(0.005)
    except (OSError, ValueError) as exc:
        error = str(exc)
    finally:
        try:
            if job:
                cleaned = job.stop()
            elif sys.platform != "win32" and process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                cleaned = True
        except OSError as exc:
            error = str(exc)
        finally:
            if job:
                job.close()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    cleaned = False
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
    status = (
        "timed_out"
        if timed_out
        else "runner_error"
        if error or not cleaned or not started
        else "capture_incomplete"
        if not complete
        else "passed"
        if code == 0
        else "failed"
    )
    return {
        "status": status,
        "returncode": code,
        "started": started,
        "seconds": round(time.monotonic() - begin, 3),
        "cleanup": {
            "scope": "windows_job" if job else "posix_process_group",
            "complete": cleaned,
        },
        "capture_complete": complete,
        "error": error,
        **{
            name: bytes(value).decode("utf-8", errors="replace")
            for name, value in tails.items()
        },
        "capture": {
            name: {
                "bytes_read": totals[name],
                "bytes_retained": len(tails[name]),
                "truncated": totals[name] > output_bytes,
            }
            for name in tails
        },
    }
