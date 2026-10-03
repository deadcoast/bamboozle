"""Synthetic subprocess fixture; NOT a production LoRA pipeline implementation."""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quarantine")
    parser.add_argument("--reason")
    parser.add_argument("--prune", action="store_true")
    args = parser.parse_args()
    if args.candidate == "continuous-output":

        def forever(stream, byte):
            while True:
                stream.write(byte * 65536)
                stream.flush()

        threads = [
            threading.Thread(target=forever, args=(sys.stdout.buffer, b"O")),
            threading.Thread(target=forever, args=(sys.stderr.buffer, b"E")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    if args.candidate in {"noisy-success", "noisy-failure", "noisy-timeout"}:

        def emit(stream, byte, ending):
            for _ in range(128):
                stream.write(byte * 65536)
            stream.write(ending)
            stream.flush()

        Path("partial.bin").write_bytes(b"synthetic output retained")
        threads = [
            threading.Thread(
                target=emit, args=(sys.stdout.buffer, b"O", b"stdout-end\n")
            ),
            threading.Thread(
                target=emit, args=(sys.stderr.buffer, b"E", b"stderr-end\n")
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if args.candidate == "noisy-timeout":
            time.sleep(60)
        return 17 if args.candidate == "noisy-failure" else 0
    if args.candidate == "closed-pipes":
        os.close(sys.stdout.fileno())
        os.close(sys.stderr.fileno())
        time.sleep(60)
    if args.candidate in {
        "descendant-pipes",
        "descendant-timeout",
        "descendant-failure",
    }:
        code = (
            "from pathlib import Path\nimport time\n"
            "deadline = time.monotonic() + 15\n"
            "while not Path('release-descendant').exists() and time.monotonic() < deadline:\n"
            "    time.sleep(.02)\n"
            "Path('descendant-finished').write_text('done')\n"
        )
        subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=sys.stdout,
            stderr=sys.stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        print("descendant owns inherited pipes", flush=True)
        if args.candidate == "descendant-timeout":
            time.sleep(60)
        return 9 if args.candidate == "descendant-failure" else 0
    if args.candidate in {"fail-after-copy", "timeout-after-copy"}:
        Path("partial.bin").write_bytes(Path("original.bin").read_bytes())
        print("copy saved; ledger not saved", flush=True)
        if args.candidate == "timeout-after-copy":
            time.sleep(60)
        return 9
    if args.candidate == "recover-copy":
        if Path("original.bin").read_bytes() != Path("partial.bin").read_bytes():
            return 10
        Path("recovery.json").write_text('{"recovered": true}', encoding="utf-8")
    if args.candidate == "output":
        sys.stdout.buffer.write(b"x" * 9000 + b"\xff")
        sys.stderr.buffer.write(b"y" * 5000 + b"\xff")
        return 0
    print(json.dumps({"args": sys.argv[1:], "cwd": str(Path.cwd())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
