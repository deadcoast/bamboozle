"""Synthetic subprocess fixture; NOT a production LoRA pipeline implementation."""

import argparse
import json
import sys
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
