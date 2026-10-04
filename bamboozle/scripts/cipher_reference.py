"""Qualify product transforms against the original C codex through a guarded build.

Uses a supplied GCC-compatible compiler. All generated files belong to a new
work directory; original C assets are read only and checked against their commit.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from bamboozle.classical import CHECKPOINT, REGISTRY, Pipeline
from bamboozle.evidence import report_destination, write_report
from bamboozle.utils import sha256, utc_now

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r"""
#include "cipher.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void hex(const char *text) {
  while (*text) printf("%02x", (unsigned char)*text++);
  putchar('\n');
}
int main(int argc, char **argv) {
  if (argc != 6) return 2;
  const cipher_t *cipher = get_cipher((size_t)atoi(argv[1]));
  if (!cipher) return 2;
  cipher_params_t params = {0};
  switch (cipher->param_type) {
    case PARAM_NUMBER: params.number.value = atoi(argv[3]); break;
    case PARAM_2_NUMBERS:
      params.two_numbers.value1 = atoi(argv[3]);
      params.two_numbers.value2 = atoi(argv[4]); break;
    case PARAM_STRING:
      params.string.text = argv[5]; params.string.length = strlen(argv[5]); break;
    case PARAM_NONE: break;
    default: return 2;
  }
  char *encoded = cipher->encrypt(argv[2], &params);
  if (!encoded) return 3;
  char *decoded = cipher->decrypt(encoded, &params);
  if (!decoded) { free(encoded); return 4; }
  hex(encoded); hex(decoded);
  free(encoded); free(decoded);
  return 0;
}
"""


def guarded_run(
    command: list[str], directory: Path, env: dict[str, str], log: Path
) -> bytes:
    """Retain output; fail once and terminate this process tree on timeout."""
    process = subprocess.Popen(
        command,
        cwd=directory,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    try:
        output, error = process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(
                [
                    str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"),
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                capture_output=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
        output, error = process.communicate(timeout=10)
        log.write_bytes(output + error)
        raise RuntimeError(f"Native command timed out; retained log: {log}") from None
    log.write_bytes(output + error)
    if process.returncode:
        raise RuntimeError(
            f"Native command exited {process.returncode}; retained log: {log}"
        )
    return output


def qualify(compiler: Path, work_dir: Path) -> dict:
    compiler = compiler.resolve(strict=True)
    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True)
    env = dict(os.environ)
    env["PATH"] = str(compiler.parent) + os.pathsep + env.get("PATH", "")
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetErrorMode.restype = ctypes.c_uint
        kernel.SetErrorMode.argtypes = [ctypes.c_uint]
        kernel.SetErrorMode.restype = ctypes.c_uint
        kernel.SetErrorMode(kernel.GetErrorMode() | 0x0001 | 0x0002 | 0x8000)
    originals = {}
    for path in sorted(
        [
            *ROOT.glob("include/**/*.h"),
            *ROOT.glob("src/ciphers/*.c"),
            ROOT / "src/cipher.c",
            ROOT / "src/utils/alpha.c",
        ]
    ):
        name = path.relative_to(ROOT.parent).as_posix()
        raw = subprocess.check_output(
            ["git", "show", CHECKPOINT + ":" + name], cwd=ROOT
        )
        normalized = path.read_bytes().replace(b"\r\n", b"\n")
        if normalized != raw.replace(b"\r\n", b"\n"):
            raise RuntimeError(f"Original C asset changed from checkpoint: {name}")
        originals[name] = {
            "git_bytes_sha256": sha256(raw),
            "working_bytes_sha256": sha256(path.read_bytes()),
        }
    harness = work_dir / "reference.c"
    harness.write_text(HARNESS, encoding="utf-8")
    executable = work_dir / ("reference.exe" if os.name == "nt" else "reference")
    # One source preflight prevents a broken driver from trying every input file.
    guarded_run(
        [
            str(compiler),
            "-std=c11",
            "-I" + str(ROOT / "include"),
            "-fsyntax-only",
            str(harness),
        ],
        ROOT,
        env,
        work_dir / "preflight.log",
    )
    sources = [
        ROOT / "src/cipher.c",
        ROOT / "src/utils/alpha.c",
        *sorted(ROOT.glob("src/ciphers/*.c")),
    ]
    guarded_run(
        [
            str(compiler),
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-I" + str(ROOT / "include"),
            str(harness),
            *map(str, sources),
            "-o",
            str(executable),
        ],
        ROOT,
        env,
        work_dir / "build.log",
    )
    rows = [
        ("caesar", {"shift": -3}, "Attack, Zebra!", "-3", "0", ""),
        ("rot13", {}, "Attack, Zebra!", "0", "0", ""),
        ("atbash", {}, "Attack, Zebra!", "0", "0", ""),
        ("affine", {"a": 5, "b": 8}, "Affine Cipher!", "5", "8", ""),
        ("scytale", {"width": 3}, "ABCDEFGHIJKL", "3", "0", ""),
        ("vigenere", {"key": "LEMON"}, "Attack at dawn!", "0", "0", "LEMON"),
        (
            "beaufort",
            {"key": "FORTIFICATION"},
            "Defend the east wall!",
            "0",
            "0",
            "FORTIFICATION",
        ),
        ("gronsfeld", {"key": "31415"}, "Attack at dawn!", "0", "0", "31415"),
        ("rail-fence", {"rails": 3}, "WEAREDISCOVEREDFLEEATONCE", "3", "0", ""),
        (
            "columnar-transposition",
            {"key": "BALLOON"},
            "ABCDEFG1234567",
            "0",
            "0",
            "BALLOON",
        ),
    ]
    results = []
    indexes = list(REGISTRY)
    for number, (name, params, text, first, second, key) in enumerate(rows):
        stdout = guarded_run(
            [str(executable), str(indexes.index(name)), text, first, second, key],
            ROOT,
            env,
            work_dir / f"vector-{number}.log",
        )
        encoded, decoded = [
            bytes.fromhex(line) for line in stdout.decode("ascii").splitlines()
        ]
        pipeline = Pipeline(
            {
                "schema_version": 1,
                "steps": [
                    {
                        "cipher": name,
                        "variant": REGISTRY[name].variant,
                        "params": params,
                    }
                ],
            }
        )
        assert pipeline.transform(text.encode()) == encoded, name
        assert pipeline.transform(encoded, reverse=True) == decoded == text.encode(), (
            name
        )
        results.append(
            {
                "cipher": name,
                "encoded_hex": encoded.hex(),
                "matches_original_c": True,
                "exact_inverse": True,
            }
        )
    defects = []
    for number, (name, text, first, key) in enumerate(
        [
            ("scytale", "tail~", "3", ""),
            ("columnar-transposition", "DATA_", "0", "CAB"),
            ("polybius-square", "Jig 11", "0", ""),
        ]
    ):
        stdout = guarded_run(
            [str(executable), str(indexes.index(name)), text, first, "0", key],
            ROOT,
            env,
            work_dir / f"defect-{number}.log",
        )
        encoded, decoded = [
            bytes.fromhex(line) for line in stdout.decode("ascii").splitlines()
        ]
        assert decoded != text.encode(), name
        defects.append(
            {
                "cipher": name,
                "source_hex": text.encode().hex(),
                "c_encoded_hex": encoded.hex(),
                "c_decoded_hex": decoded.hex(),
                "original_c_roundtrip_exact": False,
            }
        )
    # Polybius coordinate mathematics agrees; framing deliberately fixes its loss.
    alphabet = "abcdefghiklmnopqrstuvwxyz"
    stdout = guarded_run(
        [
            str(executable),
            str(indexes.index("polybius-square")),
            alphabet,
            "0",
            "0",
            "",
        ],
        ROOT,
        env,
        work_dir / "polybius-coordinates.log",
    )
    encoded, decoded = [
        bytes.fromhex(line) for line in stdout.decode("ascii").splitlines()
    ]
    polybius = Pipeline(
        {
            "schema_version": 1,
            "steps": [
                {"cipher": "polybius-square", "variant": "framed5-v1", "params": {}}
            ],
        }
    )
    framed = polybius.transform(alphabet.encode())
    assert (
        b" ".join(framed[index + 1 : index + 3] for index in range(0, len(framed), 3))
        == encoded
    )
    assert decoded == alphabet.encode()
    return {
        "schema_version": 1,
        "created": utc_now(),
        "status": "passed",
        "checkpoint": CHECKPOINT,
        "compiler": str(compiler),
        "compiler_version": guarded_run(
            [str(compiler), "--version"], ROOT, env, work_dir / "compiler.log"
        )
        .decode()
        .splitlines()[0],
        "work_dir": str(work_dir),
        "originals": originals,
        "conformance": results,
        "polybius_25_coordinates_match": True,
        "retained_original_defects": defects,
        "error_dialogs_disabled": os.name == "nt",
        "process_local_runtime_path": str(compiler.parent),
        "scope": "Native compiled original C conformance for defined inputs and reproduced data-loss cases; not memory-sanitizer qualification or authentication.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    destination = report_destination(ROOT, "VIGILES_CIPHER_REFERENCE.json")
    try:
        result = qualify(args.compiler, args.work_dir)
    except (
        OSError,
        ValueError,
        RuntimeError,
        AssertionError,
        subprocess.SubprocessError,
    ) as exc:
        write_report(
            ROOT,
            destination,
            {
                "status": "failed",
                "created": utc_now(),
                "error": str(exc),
                "work_dir": str(args.work_dir),
            },
            replace=destination.exists(),
        )
        raise
    write_report(ROOT, destination, result, replace=destination.exists())
    print(
        json.dumps(
            {
                "status": result["status"],
                "conformance": len(result["conformance"]),
                "retained_defects": len(result["retained_original_defects"]),
                "evidence": str(destination),
            }
        )
    )


if __name__ == "__main__":
    main()
