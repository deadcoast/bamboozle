# Bamboozle / Vigiles

Vigiles is MIVIM's local middleware for reversible terminology translation,
authenticated file exchange, and repository integrity checks. The application is
packaged as `bamboozle`; `vigiles`, `bamboozle`, and `python -m bamboozle` share one CLI.

The native development contract is [.mivim/mivim.vigiles.md](.mivim/mivim.vigiles.md).
[VIGILES_STATUS.json](VIGILES_STATUS.json) tracks implementation and open deployment
gates. [VIGILES_VERIFICATION.json](VIGILES_VERIFICATION.json) records actual checks,
test results, and source hashes.

## Start

Run these commands from the directory containing this README and `pyproject.toml`:

```powershell
uv sync --locked --dev
uv run --locked vigiles doctor
uv run --locked vigiles validate
uv run --locked vigiles --help
```

Python 3.13 or later is required. Runtime dependencies are Cryptography and FastMCP;
linters and tests are in the development dependency group.

All file commands operate inside an existing workspace. Its default is the current
directory. To use another project:

```powershell
uv run --locked vigiles --root "C:/path/to/project" preview "example.py"
```

Paths are relative to that root, or absolute paths contained by it. Parent escapes,
symlinks, Windows junctions, alternate data streams, and protected repository state
such as `.git` and `.venv` are rejected. Output parents must already exist.
Application writes cannot replace the `.mivim` specification.

## Translation and restoration

```powershell
uv run --locked vigiles preview "example.py"
uv run --locked vigiles encode "example.py"
uv run --locked vigiles verify "example_safe.py"
uv run --locked vigiles decode "example_safe.py"
```

`preview` returns a JSON report and diff without writing files. `encode` produces
`example_safe.py` and `.example_safe.py.scm_ledger.json`; `decode` produces
`example_safe_ready.py`. Use `-o` to choose another destination. Existing outputs
and ledgers are never overwritten, and in-place conversion is rejected.

The engine matches whole terms in a single pass, longest first. It protects native
target terms with `__SCM_LIT_...__` markers and verifies exact reversibility before
publication. Unicode identifier continuations and underscores retain their
boundaries. Lowercase, uppercase, and title case are supported; ambiguous mixed
case, damaged markers, and lossy mappings fail explicitly.

UTF-8 BOMs and CR/LF bytes survive file round trips. Each ledger contains the mapping
snapshot and original/encoded SHA-256 digests. Decode uses that snapshot, so changing
the active dictionary does not strand existing conversions. It requires the encoded
file to remain unchanged. Edited-file restoration is not supported by this release.
The retained ledger remains attached to the retained encoded source.

Translation is readable substitution. It provides no confidentiality. A ledger's
unkeyed digests check consistency, not independent authenticity; use Tessera when
confidentiality and authenticated exchange are required.

Output and ledger publication are two separate filesystem operations. An error
after output publication reports `output_saved: true` and the actual `ledger_saved`
state. Files are preserved for inspection. If the ledger is missing and you still
have the exact original and dictionary:

```powershell
uv run --locked vigiles recover "example_safe.py" --original "example.py"
uv run --locked vigiles recover "example_safe.py" --original "example.py" --apply
```

Recovery defaults to a read-only preview and refuses mismatched pairs or existing
ledgers. Concurrent input changes invalidate prepared conversions. These checks are
not an operating-system sandbox against a hostile process changing filesystem
ancestors during publication.

## Dictionaries

The bundled 110-entry dictionary lives at `src/bamboozle/cipher_dict.json` and is
included in the wheel. Both legacy flat mappings and this versioned schema load:

```json
{
  "schema_version": 1,
  "mapping_version": "team-1",
  "entries": {"alpha": "beta"}
}
```

Sources and targets must be lowercase, nonempty, unique after case folding, and
disjoint. Duplicate JSON keys, non-finite numbers, reserved markers, surrounding
whitespace, NULs, and newlines are rejected. Limits are 10,000 entries and 512
characters per term. Translation files default to 16 MiB, with expansion checked
during replacement.

```powershell
uv run --locked vigiles validate --dict "mapping.json"
uv run --locked vigiles encode "example.py" --dict "mapping.json"
uv run --locked vigiles dictionary-export --dict "mapping.json" --mapping-version "team-2" -o "mapping-v2.json"
```

Exports create independent, versioned snapshots and refuse existing destinations.
Restoring a dictionary means selecting its validated snapshot with `--dict`; no
active mapping is silently replaced. Mappings and translation ledgers can contain
sensitive terminology and should remain local.

## Tessera packets

```powershell
uv run --locked vigiles keygen --key "agent.key"
uv run --locked vigiles seal "payload.bin" --key "agent.key" --recipient "reviewer" -o "packet.json"
uv run --locked vigiles open "packet.json" --key "agent.key" --recipient "reviewer" -o "delivered.bin"
```

Tessera uses AES-256-GCM with a fresh random 96-bit nonce for each packet. Packet
version, algorithm, and recipient are authenticated as associated data. Altered
ciphertext, altered headers, wrong keys, and recipient mismatches fail before
delivery. Binary payloads are supported, with a 16 MiB payload limit and a 32 MiB
serialized-packet read limit. Keys are generated exclusively and never printed.
The implementation follows [Cryptography's AEAD API](https://cryptography.io/en/latest/hazmat/primitives/aead/).

Keep the 32-byte key separate from packets. On Windows, access follows the
containing directory's ACL; on POSIX, new temporary files start with owner-only
permissions. Key possession is the trust boundary. Recipient labels are not a
directory of verified agent identities; key distribution, revocation, and packet
replay prevention remain integration work. No payload is executed or deployed
automatically.

## Structural watch

```powershell
uv run --locked vigiles baseline
uv run --locked vigiles audit
uv run --locked vigiles watch --interval 5 --count 3
```

`baseline` explicitly creates `.vigiles/baseline.json` and refuses to replace one.
`audit` reports added, removed, and changed files without modifying them. Audits
cover source/configuration/documentation extensions plus `.gitignore` and
`.python-version`, not arbitrary binary assets. Keys, caches, build outputs, legacy
archives, and private `.vigiles` state are excluded. Audits stop on filesystem links
or after 10,000 traversed entries. Reports contain paths and hashes, not file bodies.

`watch` runs visibly in the invoking terminal and emits one JSON record per scan.
Omit `--count` to run until Ctrl+C. No scheduled task, startup service, process
termination, automatic quarantine, or remote telemetry is installed.

An audit baseline is a local comparison record, not tamper-proof remote attestation.
Preserve and review it separately when accepting a new repository baseline.

## MCP integration

```powershell
uv run --locked vigiles --root "C:/path/to/project" serve
```

The server uses stdio only. It exposes `dictionary_status`, `preview_file`,
`apply_preview`, `verify_file`, and `audit_workspace`. A preview returns a
single-use token, valid for ten minutes; only 32 pending previews are retained.
Application rechecks the source, ledger, and destination before saving. A stale,
expired, or consumed token cannot authorize a write.

`src/mcp.pacer.json` is a sample for a client whose working directory is this
project. For clients started elsewhere, supply an absolute `--project` path to uv
and an absolute `--root` path to Vigiles. No external client registration or remote
server deployment is performed by installation.

## Workshop adapters

Set `VIGILES_WORKSHOP_ROOT` to an existing workshop directory. Its `scrape`
subdirectory holds the manifest, matrix, index, ledger, and BAMb5 key. The default
is `.vigiles/workshop` beneath the launch directory. `VIGILES_WORKSHOP_PYTHON`
optionally selects the interpreter for external pipeline scripts.

```powershell
$env:VIGILES_WORKSHOP_ROOT = "D:/your-workshop"
uv run --locked python -m workshop.scrape.bamb5 init
uv run --locked python -m workshop.scrape.lookup --rebuild stats
uv run --locked python -m workshop.mcp.workshop_mcp
```

The external manifest must exist before lookup can build. Taxonomy loading supports
both `canonical` and `categories` schemas; aliases use the same normalization in
both interfaces. Plaintext alias analysis needs no key. Persisted lookup indexes
require a BAMb5 key, include its content in their fingerprint, and reject manifests
with undecodable triggers rather than publishing a partial index.

BAMb5 tokens retain their existing deterministic AES-SIV format and separate key;
they are not Tessera packets. Explicit forced BAMb5 key rotation first preserves a
content-addressed backup. Rotation does not migrate old encrypted manifests.

The LoRA promotion/quarantine pipeline and its external audit script are not
shipped here. Their adapter returns `status: unavailable` when dependencies are
absent. No live ComfyUI promotion, quarantine, or scheduled job has been qualified.

## Development and verification

```powershell
uv run --locked python scripts/verify.py --package
```

This runs full Ruff lint/format checks, Mypy, Pyright, pytest, compilation, wheel
and sdist builds, and an isolated wheel installation constrained by `uv.lock`.
The installed package is exercised from another directory through its CLI and an
MCP stdio child process. Results and source digests go to `VIGILES_VERIFICATION.json`; detailed
logs remain in a unique `.test-tmp/verify-*` directory.

If the project drive is low on space, put the dependency cache, build temporary
files, and isolated wheel environment on another volume:

```powershell
$env:UV_CACHE_DIR = "E:/codex-temp/vigiles/uv-cache"
$env:TEMP = "E:/codex-temp/vigiles/tmp"
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
$env:TMP = $env:TEMP
uv run --locked python scripts/verify.py --package --work-dir "E:/codex-temp/vigiles/checks"
```

Pytest fixtures stay in the repository's `.test-tmp` by default. Use `--test-dir`
to select another filesystem supporting symlinks or Windows junctions. Windows
exclusive publication uses native no-overwrite rename, so ordinary output does
not require hard-link support. Initial disk-space and filesystem failures are
retained in `VIGILES_VERIFICATION.failed-*.json`; a later passing run does not erase
that history.

The GitHub workflow lives at `../.github/workflows/vigiles.yml`, with Windows/Linux
and Python 3.13/3.14 jobs. Local success does not imply those remote jobs have run.

Normal commands emit JSON. Exit codes: `0` success, `1` operation failure, `2`
invalid arguments, `3` audit drift or missing baseline, `130` interrupted watch.
Watch records report their own health; a bounded watch exits normally after its
requested number of scans.

Compatibility: `Bamboozletranslate`, `process_forward`, and `process_reverse` now
delegate to the shared engine. String operations no longer create/delete a shared
global ledger. Legacy `--mode/--input/--output` flags remain in
`python -m bamboozle.translate`; file-to-file operations use current strict ledgers,
while stdin/stdout translation is explicitly a text-only operation. Earlier ledger
schemas are not silently upgraded. The two `.bat` launchers now call the installed
CLI. Older design notes in `docs/` and `.legacy/` describe historical proposals,
not current runtime guarantees.
