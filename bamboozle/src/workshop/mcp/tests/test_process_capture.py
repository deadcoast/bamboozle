"""Real subprocess output, memory, deadline, and cleanup regressions."""

import os
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

from workshop import pipeline, process
from workshop.pipeline import run_script


@pytest.fixture
def scrape(tmp_path):
    fixture = Path(__file__).with_name("fixtures") / "pipeline_contract.py"
    (tmp_path / "promote.py").write_bytes(fixture.read_bytes())
    return tmp_path


@pytest.mark.parametrize(
    "mode,code,status",
    [
        ("noisy-success", 0, "completed"),
        ("noisy-failure", 17, "failed"),
        ("noisy-timeout", 124, "timed_out"),
    ],
)
def test_large_parallel_streams_have_exact_tails_and_bounded_memory(
    scrape, monkeypatch, mode, code, status
):
    peaks = []
    original = pipeline.run_captured

    def measured(*args, **kwargs):
        # Script validation has its own 16 MiB read ceiling. Measure subprocess
        # capture after that independent preflight allocation has been released.
        tracemalloc.start()
        try:
            result = original(*args, **kwargs)
            peaks.append(tracemalloc.get_traced_memory()[1])
            return result
        finally:
            tracemalloc.stop()

    monkeypatch.setattr(pipeline, "run_captured", measured)
    report = run_script(
        scrape, Path(sys.executable), "promote.py", "--candidate", mode, timeout=5
    )
    # Each child stream emits >8 MiB. A whole-stream buffer would exceed this by
    # an order of magnitude; the adapter uses fixed tails and one read chunk.
    assert len(peaks) == 1 and peaks[0] < 2 * 1024 * 1024, peaks
    assert report["status"] == status and report["returncode"] == code
    assert report["child_exited"] and report["capture"]["complete"]
    for name, byte, limit in (("stdout", "O", 8000), ("stderr", "E", 4000)):
        ending = f"{name}-end\n"
        assert report[name] == byte * (limit - len(ending)) + ending
        assert report[f"{name}_truncated"]
        assert report["capture"][name] == {
            "limit_bytes": limit,
            "bytes_read": 128 * 65536 + len(ending),
            "bytes_retained": limit,
            "eof": True,
        }
    assert (scrape / "partial.bin").read_bytes() == b"synthetic output retained"
    assert report["side_effects_possible"] and not report["retry_performed"]


@pytest.mark.parametrize("count", [0, 7999, 8000, 8001])
def test_exact_truncation_boundary_and_empty_stderr(scrape, count):
    (scrape / "promote.py").write_text(
        f"import os\nos.write(1, b'x' * {count})\n", encoding="utf-8"
    )
    report = run_script(scrape, Path(sys.executable), "promote.py")
    assert report["stdout"] == "x" * min(count, 8000)
    assert report["stdout_truncated"] is (count > 8000)
    assert report["stderr"] == "" and not report["stderr_truncated"]
    assert report["capture"]["stderr"]["bytes_read"] == 0
    assert report["capture"]["complete"] and report["child_returncode"] == 0


def test_utf8_and_invalid_bytes_use_raw_byte_tail_boundaries(scrape):
    (scrape / "promote.py").write_text(
        "import os\nos.write(1, b'\\xe2\\x82\\xac' * 3000 + b'\\xff')\n",
        encoding="utf-8",
    )
    report = run_script(scrape, Path(sys.executable), "promote.py")
    expected = (b"\xe2\x82\xac" * 3000 + b"\xff")[-8000:]
    assert report["stdout"] == expected.decode("utf-8", errors="replace")
    assert report["capture"]["stdout"]["bytes_retained"] == 8000
    assert report["capture"]["stdout"]["bytes_read"] == 9001


def test_closed_pipes_do_not_bypass_child_deadline(scrape):
    started = time.monotonic()
    report = run_script(
        scrape,
        Path(sys.executable),
        "promote.py",
        "--candidate",
        "closed-pipes",
        timeout=0.5,
    )
    assert report["status"] == "timed_out" and report["child_exited"]
    assert report["capture"]["complete"]
    assert time.monotonic() - started < 5


def test_continuous_output_cannot_starve_timeout_or_either_stream(scrape):
    started = time.monotonic()
    report = run_script(
        scrape,
        Path(sys.executable),
        "promote.py",
        "--candidate",
        "continuous-output",
        timeout=1,
    )
    assert time.monotonic() - started < 6
    assert report["status"] == "timed_out" and report["child_exited"]
    assert report["capture"]["complete"]
    for name, byte, limit in (("stdout", "O", 8000), ("stderr", "E", 4000)):
        assert report[name] == byte * limit
        assert report["capture"][name]["bytes_read"] > limit
        assert report[f"{name}_truncated"]


@pytest.mark.parametrize(
    "mode,status,code",
    [
        ("descendant-pipes", "capture_incomplete", 125),
        ("descendant-timeout", "timed_out", 124),
        ("descendant-failure", "failed", 9),
    ],
)
def test_inherited_pipes_cannot_hold_adapter_open(scrape, mode, status, code):
    started = time.monotonic()
    try:
        report = run_script(
            scrape, Path(sys.executable), "promote.py", "--candidate", mode, timeout=1
        )
        assert time.monotonic() - started < 6
        assert report["status"] == status and report["returncode"] == code
        assert report["child_exited"] and not report["capture"]["complete"]
        assert not report["capture"]["stdout"]["eof"]
        assert "descendant owns" in report["stdout"]
        if mode == "descendant-timeout":
            assert report["termination_scope"] == "direct_child_only"
        else:
            assert report["child_returncode"] == (
                9 if mode == "descendant-failure" else 0
            )
    finally:
        # The fixture descendant is intentionally outside adapter ownership.
        # Ask it to finish itself, and wait so tests leave no background writer.
        (scrape / "release-descendant").write_text("done", encoding="utf-8")
        deadline = time.monotonic() + 5
        while (
            not (scrape / "descendant-finished").exists()
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert (scrape / "descendant-finished").exists()


@pytest.mark.parametrize("fault", ["setup", "read", "interrupt"])
def test_post_spawn_failures_cleanup_owned_child_and_pipes(scrape, monkeypatch, fault):
    children = []
    original = subprocess.Popen
    original_read = os.read

    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    def read(fd, size):
        if size == process.READ_CHUNK:
            if fault == "interrupt":
                raise KeyboardInterrupt
            raise OSError("injected pipe read failure")
        return original_read(fd, size)

    def setup(*args):
        raise OSError("injected nonblocking setup failure")

    monkeypatch.setattr(subprocess, "Popen", spawn)
    if fault == "setup":
        monkeypatch.setattr(os, "set_blocking", setup)
    else:
        monkeypatch.setattr(os, "read", read)
    if fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            run_script(scrape, Path(sys.executable), "promote.py")
    else:
        report = run_script(scrape, Path(sys.executable), "promote.py")
        assert report["started"] and report["side_effects_possible"]
        assert report["status"] == "capture_failed" and report["returncode"] == 125
        assert report["capture"]["errors"] and not report["capture"]["complete"]
    assert len(children) == 1
    assert children[0].poll() is not None
    assert children[0].stdout.closed and children[0].stderr.closed


def test_failed_termination_does_not_claim_child_has_exited(scrape, monkeypatch):
    (scrape / "promote.py").write_text(
        "import time\ntime.sleep(30)\n", encoding="utf-8"
    )
    children = []
    original = subprocess.Popen

    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)

        def fail():
            raise PermissionError("injected termination failure")

        monkeypatch.setattr(child, "kill", fail)
        return child

    monkeypatch.setattr(process, "STOP_GRACE", 0.1)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    try:
        report = run_script(scrape, Path(sys.executable), "promote.py", timeout=0.1)
        assert report["status"] == "timed_out"
        assert not report["child_exited"] and report["child_returncode"] is None
        assert "termination failure" in report["termination_error"]
        assert children[0].stdout.closed and children[0].stderr.closed
    finally:
        for child in children:
            original.kill(child)
            child.wait(timeout=5)
