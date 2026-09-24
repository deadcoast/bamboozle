# Bamboozle Cipher (SCM)

## Overview

Bamboozle is a local, reversible dictionary-based text translator. It maps configured terms to alternate names for source review and restores them afterward. It provides previews, unified diffs, conversion counts, dictionary validation, missing-ledger recovery, and per-file integrity records using only the Python standard library.

It processes UTF-8 text, including identifiers, comments, and string literals. Each forward conversion checks that the unedited result reverses exactly. Edited code still needs review and testing.

## Start here

From the repository root in PowerShell:

```powershell
.\AI\dev_tools\bamboozle.ps1
```

Choose **Encode**, **Decode**, or **Verify**, then paste a file path. Paths with spaces can be pasted with or without quotes. A missing file leaves you at the path prompt so you can correct it; Enter returns to the menu. Encode and Decode show the destination, dictionary source, substitution counts, and a diff before offering **Save**, **Full diff**, **Output path**, **Refresh**, or **Cancel**. Refresh reads the files again and shows an updated preview without saving. Enter cancels at the save prompt. Replacing an existing destination requires typing `OVERWRITE`.

The launcher finds this repository's Python environment automatically and keeps relative file paths relative to your current directory. It also accepts direct commands:

```powershell
.\AI\dev_tools\bamboozle.ps1 preview ".\node.py"
.\AI\dev_tools\bamboozle.ps1 encode ".\node.py"
.\AI\dev_tools\bamboozle.ps1 verify ".\node_safe.py"
.\AI\dev_tools\bamboozle.ps1 decode ".\node_safe.py" -o ".\node_ready.py"
```

You can also run `uv run --locked python AI/dev_tools` from the repository root. An explicit `interactive` command opens the guided workflow; with no arguments and redirected input/output, the CLI prints help and exits without waiting for input.

## Install the short command

To make `bamboozle` available in the existing project environment:

```powershell
uv pip install --python .venv/Scripts/python.exe -e ./AI/dev_tools
.\.venv\Scripts\Activate.ps1
bamboozle
```

After installation, `uv run --no-sync bamboozle` also works from the repository without activating the environment. The installed package includes its dictionary and supports `python -m bamboozle`. Runtime code uses only Python's standard library.

The original `scm-cipher --mode ...` command and direct `cipher.py` invocation remain available for existing scripts. The `.bat` files are compatibility launchers; the terminal workflow above is the primary interface.

## Review and restore

Encoding `node.py` creates `node_safe.py` and `.node_safe.py.scm_ledger.json`. Review the translated file and preserve all `__SCM_LIT_...__` markers when editing. The ledger records the mapping and native terms; keep it local. Decode, Verify, and reverse Preview automatically use the saved mapping, so a later dictionary update or a missing custom dictionary file does not strand older conversions. The chosen dictionary source is shown in terminal output and JSON reports.

Use `verify` before editing to check the encoded bytes and restored original against the ledger. For edited files, use `preview --reverse` to inspect restoration, then `decode` to save it. Successful decoding removes the matching ledger unless `--keep-ledger` is set. Review and test the restored code before using it.

## Recover a missing ledger

If the encoded file was saved but its ledger is missing, and you still have the original file and matching dictionary:

```powershell
# Check the exact pair and preview the metadata to be created. No writes.
bamboozle recover node_safe.py --original node.py

# Create only the missing ledger after repeating those checks.
bamboozle recover node_safe.py --original node.py --apply
bamboozle verify node_safe.py
```

For a custom mapping, add `--dict path/to/mapping.json` to both recovery commands. With no `--dict`, recovery uses the bundled dictionary. `--dry-run` explicitly selects the default preview behavior; it cannot be combined with `--apply`. The PowerShell launcher accepts these commands too.

Recovery requires encoding the supplied original to reproduce the encoded file byte for byte, including BOM and line endings. It refuses edited or mismatched pairs, a missing original, and any existing ledger, even a damaged one. It never overwrites either text file or existing metadata. Both files must have separate paths; an in-place conversion whose original is lost cannot establish this proof.

The new version-2 ledger records `recovered: true` and the currently checked pair. This demonstrates consistency with the original you supplied, not independent proof of historical provenance. File and dictionary fingerprints are checked before publication and again afterward; concurrent changes are reported and preserved. If ledger publication succeeds but finalization fails, the error reports `ledger_saved: true` so you can inspect it with Verify.

## Dictionary Maintenance (`cipher_dict.json`)

If you need to introduce new explicit terms to your workflow, you must update the dictionary.

**Critical Rules:**

1. **Strict 1:1 Mapping:** No two source terms can map to the same target term. Invalid mappings are rejected before conversion.
2. **Valid JSON Formatting:** Ensure standard JSON syntax. Do not leave trailing commas.
3. **Lowercase Base:** Keys and values must be nonempty lowercase ASCII strings without surrounding whitespace. Source and target sets must be disjoint. Duplicate JSON keys, duplicate targets, and the reserved `__SCM_LIT_` prefix are rejected.
4. **Case and Boundaries:** Lowercase, uppercase, and title case are supported. Mixed case is rejected when a term matches. Underscores separate terms; Unicode letters, digits, combining marks, and other Python identifier continuation characters do not. For example, a mapping for `alpha` leaves `alphá` intact. Before writing a forward result, the script checks that reversing it restores the original text exactly and rejects ambiguous mappings. Unsupported case, damaged markers, and ambiguous conversions report a line and column to help locate the problem.

## Command-line use

With the environment activated:

```powershell
# Show the installed tool version.
bamboozle --version

# Validate the dictionary without reading or changing any source files.
bamboozle validate

# Preview encoding or decoding without writing files.
bamboozle preview node.py
bamboozle preview node_safe.py --reverse

# Show only changed lines, with no surrounding context.
bamboozle preview node.py --context 0

# Save the converted file and its per-file ledger.
bamboozle encode node.py -o node_safe.py

# Verify the encoded bytes and the restored original against the ledger, without writing.
bamboozle verify node_safe.py

# Reverse an edited file, or add --strict to require the unchanged original conversion.
bamboozle decode node_safe.py -o node_ready.py
```

Encode and Validate resolve the default dictionary beside `cipher.py`, regardless of the working directory. Restore and Verify use the file's ledger mapping when available, otherwise the bundled dictionary. Pass `--dict path/to/mapping.json` to select a mapping explicitly; if a ledger exists, the supplied mapping must match it. Invalid ledgers are rejected without silently falling back to another dictionary. If `--output` is omitted, forward uses `<input-stem>_safe<extension>` and reverse uses `<input-stem>_ready<extension>`. For example, reversing `node_safe.py` defaults to `node_safe_ready.py`.

| Option | Behavior |
| --- | --- |
| `--dry-run` | Convert and validate in memory, report counts, and leave outputs and ledgers untouched. |
| `--preview` / `--diff` | On Encode or Decode, include a unified diff and leave conversion files untouched. |
| `--context N` | Show a diff with N surrounding lines (default 3, minimum 0). On Encode or Decode, implies preview without saving. |
| `--force` | Explicitly permit replacing an existing destination during Encode or Decode. |
| `--json` | Emit one JSON execution report to stdout, including conversion failures. Requires an explicit command. Can include a diff. |
| `--strict` | Require a valid version-2 ledger and unchanged encoded bytes when reversing. |
| `--keep-ledger` | Preserve the ledger after reversing to a separate output, allowing repeated verification/restoration. |
| `verify FILE` | Read-only check of the dictionary, encoded hash, and restored-original hash. Requires a version-2 ledger. |
| `recover FILE --original ORIGINAL` | Preview a missing ledger's reconstruction from an exact pair; add `--apply` to create only the ledger. |
| `validate` | Read-only dictionary schema and uniqueness checks; takes no input/output paths. |

The output directory must already exist for a write. Encode and Decode refuse existing output files or forward ledgers unless `--force` is given. Decoding to a separate destination that has its own cipher ledger is rejected even with `--force`; choose another output path to preserve that encoded file and its metadata. In-place conversion also requires `--force`; `--keep-ledger` is rejected for in-place decoding because it would attach an encoded-file ledger to restored text. The legacy `cipher.py --mode ...` interface retains its existing output overwrite behavior and shares the destination-ledger protection.

Interactive Save publishes the exact text shown in the preview. It checks the source, dictionary, ledger, and destination fingerprints again immediately before saving; changed files require a fresh preview. Fingerprints are read in bounded chunks. Creating a new output uses atomic no-overwrite publication, so a file created concurrently is preserved. The ledger is checked again after saving output and before updating or removing it; a change detected then preserves the ledger and reports that output was saved. These checks do not lock other programs out of the files. Output and ledger publication remain separate operations, as described below.

An input with an existing cipher ledger is rejected for another forward pass, preventing accidental double conversion. `--dict` and `--json` may appear before or after the subcommand. Use `bamboozle --help` or `bamboozle encode --help` for command-specific options.

Successful operations and cancelled previews return exit code 0; file, dictionary, conversion, and integrity errors return 1. Invalid CLI arguments return 2 through argparse on stderr, including when `--json` was requested. Ctrl+C returns 130. End-of-input closes the guided workflow without saving a pending preview.

### JSON reports

```powershell
bamboozle encode node.py --dry-run --json
```

Reports use `schema_version: 1`. Conversion reports include `status`, `mode` (`forward` or `reverse`), input/output paths, byte counts and SHA-256 hashes, `changed`, and `counts` (`substitutions`, `literals`, and per-source-term counts). `dictionary_source` is `ledger`, `explicit`, or `default`; `dictionary` names the ledger or mapping file, and `dictionary_sha256` identifies the selected mapping. A literal count means protected native terms during encoding and restored markers during decoding. `preview --json` adds a `diff` string and `diff_context`. Diff display uses LF separators for physical CR, LF, and CRLF lines; the saved file keeps its actual line endings. Unicode separators remain part of their source line, and a missing-final-newline marker appears only for an unterminated line. A failed operation includes `error` and `output_saved`, so callers can distinguish a failed conversion from a ledger failure after output publication.

Recovery reports have `mode: recover`, `status: recovery_preview` or `recovered`, the encoded `input`, `original`, and `ledger` paths, both files' byte counts and SHA-256 hashes, and `integrity: original_pair_checked`. `ledger_saved` records ledger publication; `output_saved` remains false because recovery does not rewrite text files. Failures use `status: error` and retain these publication flags when available.

The `integrity` field states exactly what was checked:

| Value | Meaning |
| --- | --- |
| `round_trip_checked` | The forward result reverses to the input exactly. |
| `unchanged` | Encoded and restored hashes both match the version-2 ledger. |
| `edited` | Encoded bytes differ from the forward pass; ordinary reverse allows the changes. |
| `legacy_ledger` | Version-1 metadata matches, but it has no content hashes. |
| `missing_ledger` | Reverse used the supplied dictionary and inline markers without a sidecar. |

### Ledgers and compatibility

New ledgers use version 2 and record SHA-256 hashes of the original UTF-8 bytes, encoded UTF-8 bytes, and canonical dictionary. Dictionary key ordering and JSON formatting do not affect its hash. These hashes check local consistency; they do not authenticate a ledger independently of the file.

Ordinary reverse still accepts version-1 ledgers and files without ledgers, including the existing `__SCM_LIT_...__` marker format. `verify` and `--strict` require version 2. A malformed or mismatched ledger is rejected and preserved. Keep the encoded filename and matching ledger together; the stored filename must match when a sidecar is present.

UTF-8 content, its BOM if present, and existing line endings are preserved. Output files are replaced only after a complete temporary write. Output and ledger updates are separate operations: if a ledger write or temporary-file cleanup fails after output was saved, the error explicitly says so and JSON reports `output_saved: true`. A conversion that has published output cannot reuse the same prepared preview after a finalization failure.

The round-trip check covers unedited text with the same dictionary. Newly introduced, unmarked target terms in edited text are reverse-mapped too. This tool does not parse or validate Python. Version 0.3 corrects partial matches inside Unicode identifiers; older outputs affected by that bug can fail their original-hash check and need review against the original source.

## Use the conversion engine in Python

For repeated conversions with one dictionary, construct a `CipherEngine` once:

```python
from bamboozle.cipher import CipherEngine

engine = CipherEngine({"alpha": "fluid_sim"})
encoded, counts = engine.transform("alpha = 'fluid_sim'", "forward")
assert counts == {"substitutions": 1, "literals": 1, "terms": {"alpha": 1}}
restored, _ = engine.transform(encoded, "reverse")
assert restored == "alpha = 'fluid_sim'"
```

The engine validates and copies the dictionary, exposes read-only mapping views, and reuses compiled matching rules. Each call returns independent counts collected during substitution; forward conversion also verifies an exact reverse round trip. The engine performs no file writes. `engine.count(text, "forward")` counts matches without converting or proving a round trip. The existing `process_forward(text, mapping)` and `process_reverse(text, mapping)` helpers still return strings; the CLI manages files and ledgers.

For file operations, `prepare_conversion(...)` returns a `PreparedOperation` with read-only mapping access. Its text, paths, options, and fingerprint snapshot cannot be reassigned. `plan["ledger_data"]` returns a detached copy; `plan["report"]` remains available for presentation, but editing it cannot redirect a save or reset a completed operation. `add_preview_diff(plan)` decorates that report, and `apply_conversion(plan, overwrite=False)` publishes the prepared conversion after rechecking files. Plain dictionary copies cannot be applied as plans.

`prepare_recovery(encoded_path, original_path, dict_path=None)` and `apply_recovery(plan)` provide the same preview/apply separation for missing ledgers. A conversion plan cannot be used as a recovery plan or vice versa. Prepared operations are intended for sequential review and application within one process; their state is not a cross-thread or cross-process lock.

## Troubleshooting

- **Command not found:** Use `.\AI\dev_tools\bamboozle.ps1`, or install the package and activate `.venv` as above.
- **PowerShell blocks script execution:** Run `uv run --locked python AI/dev_tools` from the repository instead.
- **Destination already exists:** Preview the change, choose another `-o` path, or explicitly pass `--force`.
- **File changed since preview:** Start another preview and inspect the updated source/destination before saving. While a preview is open, use **R / Refresh** to reload it explicitly.
- **Dictionary differs from ledger:** Omit `--dict` to restore with the saved mapping, or supply the matching original dictionary. A malformed ledger still requires correction before restoration.
- **Destination has its own cipher ledger:** Choose a different output path; `--force` does not bypass this check.
- **Missing ledger:** If you have the matching original and dictionary, preview `bamboozle recover ENCODED --original ORIGINAL`. Existing ledgers are preserved; recovery has no overwrite option.

---
