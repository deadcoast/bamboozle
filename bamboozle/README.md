# Bamboozle / Vigiles

Current release: **v0.4.3 (VIGIL3)**. Bamboozle is a semantic encoder/cipher;
Vigiles provides the agent's translation interface to it. The agent reads source
through `read_safe_file`, works in the encoded vocabulary, and submits content to
`write_safe_file`. The cipher restores domain terminology when saving source locally.

Vigiles is MIVIM's local middleware for reversible terminology translation,
authenticated file exchange, and repository integrity checks. The application is
packaged as `bamboozle`; `vigiles`, `bamboozle`, and `python -m bamboozle` share one CLI.
The manual file-conversion loop, reviewed restoration, BAMb5 token encryption,
Tessera exchange, and workshop tools remain available alongside the agent interface.

The native development contract is [.mivim/mivim.vigiles.md](.mivim/mivim.vigiles.md).
[VIGILES_STATUS.json](VIGILES_STATUS.json) holds the active development plan, core
task queue, and separate external gates. [Verification](.mivim/vigiles/reports/current/VIGILES_VERIFICATION.json)
records actual checks, test results, and source hashes. Retained evidence belongs
under [.mivim/vigiles/reports/](.mivim/vigiles/reports/README.md), with latest records
in `current/` and historical/failed records in `archive/`. Keep only active planning
and task files at the root alongside normal project and steering files.

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

## Connect the agent to the cipher

Run the agent-facing stdio server with an explicit project root:

```powershell
uv run --locked vigiles --root "C:/path/to/project" serve-agent
# Equivalent dedicated entry point; a custom dictionary stays local to this root:
uv run --locked scm-mcp --root "C:/path/to/project" --dict "mapping.json"
```

Register the command with your coding agent's MCP client. For clients started
outside this application directory, use an explicit project path:

```json
{
  "mcpServers": {
    "vigiles": {
      "command": "uv",
      "args": ["run", "--project", "C:/path/to/bamboozle/bamboozle", "--locked", "vigiles", "--root", "C:/path/to/project", "serve-agent"]
    }
  }
}
```

The agent uses these two tools for source I/O:

1. `read_safe_file({"file_path": "node.py"})` returns encoded `content`, the source
   hash, and the terminology contract. It does not return the inverse dictionary,
   a raw-source diff, or create a `_safe` file the operator has to shuttle around.
2. The agent edits that content, keeping mapped identifiers and literal markers.
3. `write_safe_file({"file_path": "node.py", "content": "...neutral code..."})`
   decodes and saves the edited source. The agent can also create a new module by
   supplying a new filename; no original counterpart is required.

For existing files, the server remembers up to 256 read hashes and refuses changed
or deleted sources. After reconnecting or eviction, read again or supply
`expected_sha256` from the earlier read. Before editing an existing file, the writer
retains its exact prior bytes under `.vigiles/cipher/changes/<operation_id>/original.bin`,
along with the decoded output, intent, and completion record. Write responses report
hashes and publication state, without returning decoded source. On partial failure,
inspect the reported history path; `output_saved` states whether publication occurred.
No rollback or automatic deletion of retained versions occurs.

The dedicated server exposes only those two cipher tools. It refuses access to its
configured dictionary, cipher ledgers, key files, and private history. Dictionary
substitution covers mapped terms; it is not general secret redaction or OS isolation.
The per-server write lock and repeated hash checks are not a filesystem transaction
against unrelated processes racing publication. Existing parent directories are
required. Use the broader `serve` command for the retained operator preview/review,
migration, verification and audit tools; those operator tools can display domain text.

The public Python cipher interface is available to metadata pipelines too:

```python
from bamboozle.cipher import CipherEngine, load_json

cipher = CipherEngine(load_json("cipher_dict.json"))
agent_input = cipher.encode(source_text)
restored_source = cipher.decode(agent_output)
```

`process_forward` and `process_reverse` remain available for stream integrations.
`scm-cipher --mode forward --input source.py --output source_safe.py` and
`python -m bamboozle.cipher` expose the original flag interface over the shared engine.

## Manual translation and restoration

```powershell
uv run --locked vigiles preview "example.py"
uv run --locked vigiles encode "example.py"
uv run --locked vigiles verify "example_safe.py"
# Let the agent edit example_safe.py, preserving literal and case markers.
uv run --locked vigiles preview "example_safe.py" --reverse
uv run --locked vigiles decode "example_safe.py"
```

`preview` returns a JSON report and diff without writing files. `encode` produces
`example_safe.py` and `.example_safe.py.scm_ledger.json`; `decode` produces
`example_safe_ready.py`. Use `-o` to choose another destination. Existing outputs
and ledgers are never overwritten, and in-place conversion is rejected.

The engine matches whole terms in a single pass, longest first. It protects native
target terms with `__SCM_LIT_...__` markers and verifies exact reversibility before
publication. Unicode identifier continuations and underscores retain their
boundaries. Lowercase, uppercase, title case, sentence case, and mixed capitalization
are preserved. Mixed case uses `__SCM_CASE_<hex-case-mask>_<neutral-term>__` when plain
replacement would lose information. Keep both marker types intact in agent edits.
The mask records uppercase character positions; it does not contain the original
term. These markers require decoder v0.4.3 or later. Damaged markers and lossy
Unicode case folds fail explicitly.

UTF-8 BOMs and CR/LF bytes survive file round trips. Each ledger contains the mapping
snapshot and original/encoded SHA-256 digests. Decode uses that snapshot, so changing
the active dictionary does not strand existing conversions. **Normal decode accepts
agent edits.** Newly introduced unmarked target terms use the translated vocabulary
and reverse-map too. Keep or add `__SCM_LIT_target__` markers when a mapped target
term must remain literal. The original source is not required to decode agent output.
Keep inverse dictionaries and ledgers local when sharing neutral files with an agent.

If the agent returns a new filename or generates a new module from a translated
specification, select the saved mapping from that specification's encoding:

```powershell
uv run --locked vigiles encode "task.md" --dict "mapping.json"
# Give task_safe.md to the agent; save its new code as AGENT_fix.py.
uv run --locked vigiles preview "AGENT_fix.py" --reverse --ledger ".task_safe.md.scm_ledger.json"
uv run --locked vigiles decode "AGENT_fix.py" --ledger ".task_safe.md.scm_ledger.json" -o "module_ready.py"
uv run --locked python "module_ready.py"
```

`--dict mapping.json` can supply the mapping directly without a ledger. When a
ledger is present, an explicit dictionary must match it. With neither a ledger
nor `--dict`, decode uses the packaged dictionary, as the original drag-and-drop
workflow does. Reports identify `mapping_source`, `dictionary_sha256`, and the
selected `ledger_input`. Corrupt or unsupported ledgers fail explicitly; they do
not trigger a fallback. Generated output does not need an original counterpart.

`src/ENCRYPT_FOR_AGENT.bat` and `src/DECRYPT_FROM_AGENT.bat` accept a dragged file
using the packaged mapping; the return launcher accepts edits and new filenames.
Use the CLI's `--ledger` or `--dict` for a custom mapping on a newly named return.

Use `decode --strict` when you specifically require the unchanged encoding and an
exact original round trip. `verify` always performs that strict check, so it will
report agent edits as changed. Normal decode reports translation and publication;
it does not claim the changed output matches the original digest. The ledger stays
with its encoded source. Review and run the returned program's tests before integration.

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

## Optional reviewed restoration

Use this additional workflow when you want to resolve changed-line terminology
individually, compare with retained original source, and retain a provenance receipt.
Normal `decode` above remains the direct agent-return path.

Keep the original source and its translation ledger. Edit the encoded file, then
preview restoration using the saved mapping and the retained original:

```powershell
uv run --locked vigiles encode "node.py"
# Edit node_safe.py with your editor or coding agent.
uv run --locked vigiles restore-edits "node_safe.py" --original "node.py"
```

The original must reproduce the ledger's encoded baseline and original digest.
The active dictionary is irrelevant. `--ledger PATH` selects a retained native
ledger explicitly when the edited file was copied to another filename.

Unchanged complete line occurrences retain their original translation meaning.
Added or changed lines containing unmarked target terms return `status: conflict`
and exit code 3, with one-based `line:column` positions, the term, and its possible
translation. Additional copies beyond the baseline's line counts need review too.
These comparisons establish textual consistency; they cannot infer author intent.
Removing a literal marker turns its term into a conflict on the changed line.
Malformed markers must be repaired or removed completely before restoration.

When all unmarked terms on changed lines use the translated vocabulary, select
`--terms translate`. Use `--terms literal` to preserve those changed-line terms.
Native literal markers always restore their literal values. For mixed intent,
create a JSON decisions file from the conflict positions:

```json
{
  "schema_version": 1,
  "edited_sha256": "the edited_sha256 returned by the preview",
  "choices": {"4:8": "translate", "7:12": "literal"}
}
```

Pass it with `--decisions choices.json`. Decisions override the selected policy,
must name changed-line target positions, and are invalidated by any edited-file
change. Unsupported case conversions require literal preservation or a corrected
edit. Unresolved conflicts cannot be applied.

Review the original-to-restored diff and apply its exact digest:

```powershell
$review = uv run --locked vigiles restore-edits "node_safe.py" --original "node.py" --terms translate | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Restoration preview failed" }
$review.diff
uv run --locked vigiles restore-edits "node_safe.py" --original "node.py" --terms translate --apply --review-sha256 $review.review_sha256
uv run --locked vigiles verify-restoration "node_safe_ready.py"
# Run the restored program's normal tests before integrating its changes.
```

`-o` chooses a separate destination. Original, edited input, ledger, and decisions
remain unchanged. Application rechecks all input hashes and refuses existing
outputs, ledgers, and receipts. `.node_safe_ready.py.vigiles-restore.json` records
input/output hashes, mapping identity, baseline identity, and the resolved choices.
`verify-restoration` checks the output, receipt, and retained input hashes; it does
not claim the edited program has correct semantics or authenticate its author.

Output and receipt are separate publications. A failure reports `output_saved`
and `metadata_saved` accurately and preserves published bytes. If only metadata is
missing, repeat the same preview with `--recover-metadata`, then apply that review
with the same flag. Recovery requires the existing output to equal the reviewed
result and refuses an existing receipt. It never rewrites the output.

Metadata and each text input/output retain the 16 MiB workspace limit. At most
10,000 changed-line target occurrences are processed; the first 100 conflicts are
returned with a total and omitted count. Resolve shown positions in batches, or
select an explicit term policy. Diff generation is bounded to 256 KiB of combined
input, four million line comparisons, and 64 KiB of returned UTF-8 text. A partial
or omitted diff is labeled `diff_complete: false`; hashes cover the complete result.

## Migrate a historical translation

`migrate-ledger` accepts the retained standalone version-1/version-2 per-file
ledgers and the original global `.scm_ledger.json` format. Supply the **unedited**
historical encoded file and original. Global records also require the exact
dictionary, because they never stored one:

```powershell
$review = uv run --locked vigiles migrate-ledger "old_safe.py" --original "old.py" --ledger ".scm_ledger.json" --dict "old-dictionary.json" | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Migration preview failed" }
$review
uv run --locked vigiles migrate-ledger "old_safe.py" --original "old.py" --ledger ".scm_ledger.json" --dict "old-dictionary.json" --apply --review-sha256 $review.review_sha256
uv run --locked vigiles verify "old_safe_migrated.py"
```

Migration replays the historical encoder, checks available digests and file
identities, and creates a separate native encoding and ledger. Standalone records
retain their historical underscore boundary rules during replay; the new encoding
uses native rules and reports `encoding_changed`. Global replay accounts explicitly
for historical universal-newline I/O, while the new encoding preserves the supplied
original bytes. Weak historical records establish pair consistency only. Missing,
edited, incompatible, or corrupt evidence is refused, never silently upgraded.
Historical files remain intact. The migrated file supports strict decoding and the
edited-file workflow above. Missing migrated metadata uses `--recover-metadata`.

## Dictionaries

The default dictionary now has **415 entries** (mapping version `2`), combining the
unchanged 110 original domain mappings in `src/bamboozle/cipher_dict.json` with
`src/bamboozle/profanity_dict.json`. The latter supplies a **310-entry profanity
profile** (`profanity-1`); five mappings overlap identically. Both resources ship in
the wheel. Existing ledgers retain their own complete dictionary snapshots.

The additions cover common profanity, discriminatory slurs, plurals, inflections,
possessives, compounds, phrases, regional forms, abbreviations, curly apostrophes,
and explicitly listed disguised spellings such as `f**k`, `sh!t`, and `b1tch`.
Each spelling has a distinct neutral target so decoding restores the exact spelling.
No stemming, fuzzy normalization, or guessed reverse mapping is performed.

Use `--profile profanity` to omit the original adult-domain vocabulary, including
ambiguous technical terms such as `penetration`, `wet`, `cnc`, `dp`, and `atm`:

```powershell
uv run --locked vigiles scan "node.py" --profile profanity --max-matches 100
uv run --locked vigiles encode "node.py" --profile profanity
uv run --locked vigiles decode "node_safe.py" --strict
uv run --locked vigiles --root "C:/path/to/project" serve-agent --profile profanity
uv run --locked vigiles dictionary-export --profile profanity -o "my-profanity.json"
```

`scan` writes no files. Its JSON includes total matches, per-term counts, replacements,
and one-based line/column locations, plus zero-based Unicode character offsets with
an exclusive end. It recognizes CRLF, LF, and CR. `--max-matches` bounds returned
locations (1–10,000, default 1,000); totals remain complete and `matches_complete`
reports truncation. The operator MCP server exposes the same service as `scan_file`;
the dedicated agent server continues to expose only encoded reads and decoded writes.

Matching is lexical, not a judgment about intent. Ordinary words such as `class`,
`assignment`, and `Scunthorpe` are not substring matches. Compound identifiers such
as `ass_count` remain whole identifiers. Unlisted disguised spellings and other
languages are not automatically recognized; a zero-match scan means no dictionary
matches, not universal profanity clearance. Edit an exported dictionary to add
project-specific terms or remove context-dependent entries, then select it with
`--dict`. Profile and custom-dictionary options are mutually exclusive.

Profiles also work with `preview`, `validate`, `doctor`, `recover`, `serve`,
`scm-mcp`, and `scm-cipher`. For Python callers use
`Dictionary.load(profile="profanity")` and `CipherEngine(mapping).scan(text)`.
When decoding an existing ledger, omit profile selection to use the saved mapping;
an explicitly selected profile must match that snapshot. Use the same selected
profile when decoding a new agent return without a ledger.

Both legacy flat mappings and this versioned schema load:

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

For a normal agent return, call `preview_file` with `reverse: true`, optionally
`ledger: ".task_safe.md.scm_ledger.json"` or `dictionary: "mapping.json"`, then
`apply_preview` with the returned `plan_id`. `strict: true` requests unchanged-file
verification. A saved mapping takes precedence over the server's default mapping;
an explicitly supplied dictionary must match that ledger.

The server uses stdio only. It exposes `dictionary_status`, `scan_file`, `preview_file`,
`apply_preview`, `verify_file`, and `audit_workspace`. A preview returns a
single-use token, valid for ten minutes; only 32 pending previews are retained.
Application rechecks the source, ledger, and destination before saving. A stale,
expired, or consumed token cannot authorize a write.

`preview_restoration(source, original, output, ledger, terms, decisions)` and
`preview_migration(source, original, ledger, output, dictionary)` use the same
reviewed services as the CLI. Optional `recover_metadata` selects explicit recovery
of a retained output's missing metadata. A conflict response has no application
token. Successful previews use the existing `apply_preview(plan_id)` lifecycle;
`verify_restored_file(source)` verifies a restoration receipt. These tools belong
to the primary Vigiles server; workshop servers continue to own workshop operations.

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

The native pipeline is shipped in `workshop.scrape.promote` and
`workshop.scrape.audit_matrix`. Initialize a **new or empty** workshop to install
entry-point scripts, empty manifest/matrix, taxonomy, and a local BAMb5 key:

```powershell
uv run --locked vigiles workshop-init --workshop "D:/your-new-workshop"
```

Initialization refuses existing contents. External pipelines remain supported;
their adapter returns `status: unavailable` when dependencies are absent.

Before connecting an external pipeline, run the read-only dependency check:

```powershell
uv run --locked vigiles workshop-check --workshop "D:/your-workshop"
```

Omit `--workshop` to inspect `VIGILES_WORKSHOP_ROOT` or its default. Optional
`--python` selects the interpreter to inspect. This command does not create the
workshop, execute/import its scripts, or write an index. It checks the interpreter
file, Python syntax and hashes of `scrape/promote.py` and `scrape/audit_matrix.py`,
the `loras` manifest shape, and distinct sorted `A|B` matrix pairs referencing
existing IDs. Missing files, invalid fields, duplicate JSON keys, and non-finite
JSON values produce `needs_attention` and exit code `3`. Passing inspection yields
`ready_for_review`, never deployment qualification.

Both workshop MCP interfaces expose the same read-only `pipeline_readiness` tool
and share `workshop.pipeline.run_script`. Existing script flags are preserved:
promotion defaults to `--dry-run`, quarantine passes `--quarantine/--reason`, and
audit optionally passes `--prune`. The adapter uses explicit arguments and no shell,
records the script digest, and never retries a failed operation automatically.
It distinguishes `completed`, `failed`, `timed_out`, `start_failed`, `unavailable`,
`capture_failed`, and `capture_incomplete`.
An executed script may have side effects even with `--dry-run` or a nonzero exit.
Timeout reports preserve captured progress and partial files for inspection.
Capture continuously drains both pipes using nonblocking reads. It retains only
the last 8,000 stdout bytes and 4,000 stderr bytes, with reads capped at 64 KiB.
Retention stays bounded as output grows; discarded prefixes are not spooled to
disk. UTF-8 decoding happens after capture, replacing invalid or split sequences.
The limits describe raw retained bytes, not the encoded size of the JSON response.

The existing `stdout_truncated` and `stderr_truncated` fields indicate discarded
prefixes. The new `capture` object records each stream's byte limit, bytes read,
bytes retained, and EOF state. `capture.complete` means both streams reached EOF
without a pipe error; it may be true while both tails are truncated. Byte counts
cover observed bytes only when capture is incomplete.

Child exit and pipe EOF are tracked separately. Once the direct child exits, the
adapter permits up to one further second of draining. A descendant retaining an
inherited pipe cannot hold the caller indefinitely. A zero child exit with missing
EOF yields `capture_incomplete` and adapter return code `125`. Pipe errors yield
`capture_failed`/`125`. Nonzero child exits remain `failed` with their original code,
with any incomplete capture shown separately.

Timeouts retain code `124`, request termination of the direct child, and allow up
to two seconds of cleanup; normal scheduling may add latency. Process creation is
outside that deadline. `child_returncode`, `child_exited`, and `pid` identify the
actual process outcome. A failed termination is reported explicitly. Interrupts
clean up the owned child and pipes before propagating. No reader threads remain.
External process trees and child CPU/memory/disk limits belong to host qualification.
The output limit does not limit the child's resource use or the existing bounded
script-validation allocation.

The subprocess fixtures in `src/workshop/mcp/tests/fixtures` exercise the adapter
contract, including interrupted publication and explicit fixture recovery. They
are synthetic, are excluded from the wheel, and do not implement or qualify LoRA
promotion. [Pipeline readiness](.mivim/vigiles/reports/current/VIGILES_PIPELINE_READINESS.json)
records the checked local dependencies.

## Native pipeline

Source authority is `src/workshop/scrape/promote.py` and
`src/workshop/scrape/audit_matrix.py`, sharing `src/workshop/operations.py`.
Generated scripts in the workshop's `scrape` directory call the installed modules
with an explicit root. Native readiness also checks key length and pending journals.

Put artifacts and metadata under `candidates`. Candidate JSON requires
`schema_version: 1`, `id`, `artifact` (a workshop-relative `.safetensors` or `.bin`
path), `sha256`, `validation` (a workshop-relative JSON path), and `base_model`.
Optional `triggers`, `trained_words`, `tags`, and `categories` are string lists.
IDs allow 1–64 letters, digits, underscores, and hyphens, starting with a letter
or digit. The generated example candidate files demonstrate the complete schema.

After actual evaluation, the validation record must declare `passed: true`, the
matching `artifact_sha256`, and `kind: "external_evaluation"`. Synthetic examples
use `kind: "fixture"`. These records describe operator-supplied evidence; the file
pipeline does not evaluate tensors or authenticate the record's author. Operations
keep `model_qualified: false`.

```powershell
$env:VIGILES_WORKSHOP_ROOT = "D:/your-new-workshop"
uv run --locked python -m workshop.scrape.promote --candidate "candidates/MyLoRA.json" --dry-run
uv run --locked python -m workshop.scrape.promote --candidate "candidates/MyLoRA.json"
uv run --locked python -m workshop.scrape.audit_matrix
uv run --locked python -m workshop.scrape.audit_matrix --prune --apply
uv run --locked python -m workshop.scrape.promote --quarantine MyLoRA --reason "review required" --dry-run
uv run --locked python -m workshop.scrape.promote --quarantine MyLoRA --reason "review required"
uv run --locked python -m workshop.scrape.promote --status
```

Promotion verifies copied bytes and preserves candidates. Existing IDs and outputs
are refused. Quarantine verifies a separate copy, removes the active manifest entry
and matrix pairs, then removes the matching production copy. Before-images retain
removed metadata. Assets are streamed with an 8 GiB cap; metadata and each journal
retain the 16 MiB limit.

Audit is read-only unless `--apply` is supplied. It refreshes overlap, including
zero overlap, using the workshop taxonomy/key; existing scores and notes survive.
New overlap pairs are labeled `untested`. `--prune` selects dangling pairs for
removal, never models. Undecodable triggers prevent publication. Native audit
limits inputs to 1,000 active LoRAs and 50,000 existing pairs.

An OS file lock excludes other native writers. Multi-file publication can be
interrupted. Native queries, lookup/index builds, readiness checks, and new
operation previews accept captured metadata only when the same completed journal
set surrounds the read. Pending operations reject these reads before data is
returned or an index is built. Publication completing during a read also rejects
that read; retry explicitly. This works with existing v0.3.0 workshops without
creating reader locks or migrating their files.

`pipeline_status` remains available for recovery inspection and includes
`readable`. A requested index rebuild is blocked while recovery is pending.
Each query reflects a completed snapshot, not a reservation against a future
publication. Stack checks use the index and compatibility matrix from the same
snapshot. Index fingerprints and encryption use the captured manifest, taxonomy,
key, and native matrix bytes. Native lookup includes triggers, trained words, and
tags, matching the audit inputs. Ledger queries accept 1–1,000 rows.

These guarantees cover cooperating native journaled writers. External pipelines
and manual changes that bypass journals do not provide transaction consistency.
Snapshots and cache reads retain per-file size limits and strict JSON parsing;
inspection creates no cache, lock, or directory. Journals retain input hashes and
before/after metadata. Completion markers bind to journal digests, and pending
operations block subsequent writes:

```powershell
uv run --locked python -m workshop.scrape.promote --recover <operation-id> --dry-run
uv run --locked python -m workshop.scrape.promote --recover <operation-id>
```

Recovery resumes matching states without duplicate ledger rows and refuses
unrelated changes. There is no automatic rollback. Both workshop MCP interfaces
expose `recover_pipeline`, `quarantine_lora(..., dry_run=True)`, and
`run_audit(..., apply=True)`. Promotion defaults to preview; the existing quarantine
tool retains its explicit-action default, so request `dry_run=True` for a preview.

### Diagnose an interrupted native operation

Use the operation ID returned by a failed publication, `--status`, or plan
inventory. Select the same workshop that owns the retained journal:

```powershell
uv run --locked python -m workshop.scrape.promote --root "D:/your-workshop" --diagnose-recovery <operation-id>
# After reviewing a recoverable diagnosis, preview and explicitly resume:
uv run --locked python -m workshop.scrape.promote --root "D:/your-workshop" --recover <operation-id> --dry-run
uv run --locked python -m workshop.scrape.promote --root "D:/your-workshop" --recover <operation-id>
```

Both workshop MCP servers expose `diagnose_recovery(operation_id)`. A successful
diagnostic invocation returns exit code zero even when it finds conflicts; inspect
the report's `status` and `recovery_ready`, not just the subprocess return code.
Invalid arguments or an unavailable workshop root return the normal command error.

| Diagnostic status | Meaning and next action |
| --- | --- |
| `recoverable` | All observed recovery preconditions match and no other journal blocks recovery. Review the report, then use explicit recovery. |
| `conflict` | Changed/missing required files or other pending/damaged operations block recovery. Inspect every listed path and blocker, preserving changed bytes. |
| `corrupt` | Selected retained evidence has invalid structure, mismatched images/digests, a missing required reviewed plan, or conflicting cancellation/completion records. Preserve it and compare with trusted evidence. |
| `unavailable` | Access, regular-file, read, catalog, or response limits prevented a complete diagnosis. Resolve the stated limitation before retrying explicitly. |
| `changing` | Observed files or transaction membership changed during inspection. Wait for publication to stop, then explicitly inspect again. |
| `already_completed` | Valid retained completion evidence exists. No recovery is needed; current files may reflect later operations. |
| `not_started` / `cancelled` | A retained plan has no journal. Review an unused plan with `--show-plan`; preserve cancelled plans without applying them. |
| `not_found` | Check the operation ID and selected workshop. |

Reports contain paths, digests and states, never control-file bodies or key bytes.
`inputs` must be `matching`. A control is `pending` when it matches its before-image,
`matching` when it already matches its after-image, or `conflict` otherwise. Asset
rows compare streamed content hashes: `pending` destination means a copy remains;
`removed` source means a quarantine copy is valid and production removal already
occurred. All relevant mismatches are collected instead of stopping at the first.

Diagnostics create no locks, directories, caches, files, or repairs. Reads of
partial state are confined to this diagnostic surface; normal consumers still
reject pending publication. Each report reads at most 64 MiB of metadata, with a
16 MiB per-file cap, scans at most 10,000 transaction entries per scan, and hashes
at most two assets of 8 GiB each with a fixed-size buffer. It rechecks observed file
identities, sizes and modification times, including missing records. Reports fit
6,000 ASCII JSON bytes. If full details cannot fit, an explicit `unavailable`
response reports the required size and withholds readiness; output is never
silently truncated. Other-operation blocker examples are limited to five, with
total and omitted counts reported.

`recovery_ready` describes observed preconditions. `writer_lock_checked: false`
means it is not a reservation or permission to write. Recovery rechecks under the
native writer lock. Hostile changes that preserve observed file metadata remain
outside application isolation. Completed operations are checked as historical
records without comparing their old images against newer live files.

Recovery and diagnosis share journal structure validation: action-specific control
writes, valid images and digests, required fields, and at most 16 input
preconditions. A conflicting cancellation marker blocks explicit recovery too.
Genuine journals generated by prior native releases remain supported.

```powershell
uv run --locked python scripts/qualify_recovery.py --workshop ".vigiles/new-recovery-qualification"
```

[Recovery qualification](.mivim/vigiles/reports/current/VIGILES_RECOVERY_QUALIFICATION.json)
records real CLI diagnostics and explicit recovery for promotion, quarantine, and
audit. Separate conflict and corruption workshops remain deliberately damaged for
inspection. Installed-wheel verification also exercises both real MCP stdio
servers before recovery, with multiple conflicts, and after completion.

### Prepare, review, and apply the same native plan

Preparation writes a plan under `scrape/prepared` and takes the native writer lock;
it does not publish assets, controls, or a transaction journal. Keep the returned
`operation_id` and `plan_sha256`. Inspection and application previews are read-only:

```powershell
$review = uv run --locked python -m workshop.scrape.promote --prepare-candidate "candidates/MyLoRA.json" | ConvertFrom-Json
uv run --locked python -m workshop.scrape.promote --show-plan $review.operation_id --details
uv run --locked python -m workshop.scrape.promote --apply-plan $review.operation_id --plan-sha256 $review.plan_sha256 --dry-run
# Apply only after reviewing the saved plan:
uv run --locked python -m workshop.scrape.promote --apply-plan $review.operation_id --plan-sha256 $review.plan_sha256
```

Use `--prepare-quarantine MyLoRA --reason "review required"` to prepare quarantine.
`--show-plan` reports source/destination, asset hash, reason, input hashes, control
before/after hashes, and current applicability. `--details` also prints the full
control before/after text for local review. This larger output should be read
directly from the CLI; adapter output capture is bounded.

Application requires the exact reviewed file digest and selected workshop. Under
the writer lock it rechecks candidate/validation bytes, asset hashes, and every
captured control. A destination that appeared since preparation is rejected even
if its bytes match. A consumed or cancelled plan cannot publish again. The saved
plan bytes become the transaction journal unchanged; an interrupted apply requires
the explicit `--recover` workflow above. Recovery verifies the retained plan against
the journal and accepts only matching before/after states.

There are at most 32 unused plans per workshop. Cancel a stale or unwanted plan
explicitly with `--cancel-plan <id> --plan-sha256 <reviewed-hash>`. Cancellation
retains the plan and a digest-bound marker; it does not roll back pending or
completed operations. Plans do not expire automatically. History remains on disk,
so this bounds unused plans rather than total lifetime storage. Existing native
workshops create the prepared directory lazily.

Both workshop MCP interfaces expose `prepare_promotion`, `prepare_quarantine`,
`inspect_plan`, `apply_plan`, and `cancel_plan`. These are native capabilities;
external scripts need their own implementations of these options. MCP inspection
returns the bounded summary, and `apply_plan` defaults to `dry_run=True`.
The summary is capped at 6,000 ASCII JSON bytes before preparation; each saved
plan retains the 16 MiB file limit. Oversized summaries fail without saving a plan.
If saving succeeds but temporary cleanup fails, `prepared_cleanup_failed` includes
the saved ID and digest for inspection; no automatic retry occurs.

Legacy `--candidate`/`--quarantine` commands still create and apply a fresh plan
unless `--dry-run` is supplied. A legacy dry-run does not create a reusable plan.
Local digests establish consistency, not authenticated human approval. Changes by
uncoordinated or hostile processes remain outside native writer serialization.

```powershell
uv run --locked python scripts/qualify_plans.py --workshop ".vigiles/new-plan-qualification"
```

[Plan qualification](.mivim/vigiles/reports/current/VIGILES_PLAN_QUALIFICATION.json)
records actual CLI preparation, stale rejection,
controlled interrupted application, exact-byte recovery, replay rejection, and
quarantine/cancellation using synthetic assets at `.vigiles/plans-v0.3.5`.

### Discover retained plans

Use inventory after losing a client response, or to locate stale plans occupying
unused slots. It reads existing files without creating locks, directories, or caches:

```powershell
$page = uv run --locked python -m workshop.scrape.promote --list-plans --limit 5 | ConvertFrom-Json
$page.items
if ($page.has_more) {
    uv run --locked python -m workshop.scrape.promote --list-plans --limit 5 --cursor $page.next_cursor
}
```

Both workshop MCP interfaces expose `list_plans(limit=5, cursor=None)`. Continue
with the returned cursor until `has_more` is false. Pages contain up to 10 records
and 6,000 ASCII JSON bytes; long filenames can reduce a page's record count.
Each row names the retained file; valid plans include their stable operation ID,
exact digest, action, LoRA, creation time, and observed state:

| State | Meaning |
| --- | --- |
| `prepared` | Retained unused plan; checked metadata currently matches. |
| `stale` | Input/control metadata, source presence, or destination presence changed. |
| `cancelled` | Retained cancellation marker matches the plan digest. |
| `recovery_required` | The matching journal lacks valid completion. |
| `applied` | The completion record matches the exact plan journal. |
| `corrupt` | Invalid record, unexpected filename, orphan cancellation marker, or mismatched completion/cancellation. |
| `unavailable` | Read access or the page read budget prevented inspection. |
| `changing` | Observed metadata changed while the row was being read. |

Inventory reports `freshness: metadata_only` and `apply_preflight: false`. It does
not hash model assets, check every other operation's recovery state, or reserve a
workshop. A `prepared` row is not permission or proof of applicability. Use
`--show-plan <id>` for full inspection, and review its details before digest-bound
application. Apply always retains full asset and state validation.

Each page reads at most 64 MiB of metadata, including the native workshop marker,
and scans at most 10,000 directory
entries per scan. Read-budget exhaustion shortens the page; if one record cannot
be inspected within the budget, that row reports `unavailable`. Try a smaller page
or inspect its ID. Exceeding the directory cap fails explicitly without returning
a partial inventory. Known cancellation markers are folded into their plan row;
orphans, unexpected files, and directories remain visible.

Cursors bind to the workshop and sorted directory membership, not a stored
snapshot of model/control data. Addition, removal, or rename invalidates an old
cursor; restart listing explicitly. Changes in membership during a page also fail
that page. Existing rows' states are observed anew on each request; this is not a
transactional snapshot across pages. No history is cancelled or deleted by listing.

```powershell
uv run --locked python scripts/qualify_inventory.py --workshop ".vigiles/new-inventory-qualification"
```

[Inventory qualification](.mivim/vigiles/reports/current/VIGILES_INVENTORY_QUALIFICATION.json)
records real CLI paging, damaged-record
visibility, cursor invalidation, and discovery during interrupted publication.
Its synthetic workspace, `.vigiles/inventory-v0.3.5`, deliberately retains a
corrupt plan fixture as evidence; use the separate pipeline fixture for normal work.

Reproduce qualification with explicitly synthetic byte assets in a new directory:

```powershell
uv run --locked python scripts/qualify_pipeline.py --workshop ".vigiles/new-qualification"
```

The qualifiers require new or empty workshops and use managed current/archive
report storage by default. An explicit `--report` must name a new nested JSON path.
The pipeline qualifier exercises the actual scripts, injects a controlled ledger
failure, and recovers through the CLI. This
checkout's current target is `.vigiles/workshop-v0.3.5`; prior targets remain
at `.vigiles/workshop` and the `.vigiles/workshop-v0.3.1` through `v0.3.4` paths. See
[Pipeline qualification](.mivim/vigiles/reports/current/VIGILES_PIPELINE_QUALIFICATION.json)
for representative manifest/matrix data and
execution evidence. Fixture success does not establish live LoRA/ComfyUI acceptance.

The installed-wheel smoke test also checks pending-read rejection and recovery
through a real workshop MCP stdio process, and noisy external-script success,
failure, and timeout using the installed adapter. Both workshop MCP stdio servers
also exercise prepared promotion/quarantine, inspection, preview, replay rejection,
and cancellation, plus read-only paged discovery of valid, stale, and corrupt
records. Historical reports, including versioned status snapshots and failed runs,
are retained under `.mivim/vigiles/reports/archive/`. The
[relocation index](.mivim/vigiles/reports/INDEX.json) maps former root filenames to
unchanged archived bytes and their hashes. Embedded paths in historical records
retain their original application-root meaning; use the index for relocated reports.

## Development and verification

```powershell
uv run --locked python scripts/verify.py --package
```

This runs full Ruff lint/format checks, Mypy, Pyright, pytest, compilation, wheel
and sdist builds, and an isolated wheel installation constrained by `uv.lock`.
The installed package is exercised from another directory through its CLI and an
MCP stdio child process. Results and source digests go to
`.mivim/vigiles/reports/current/VIGILES_VERIFICATION.json`; detailed logs remain in a
unique `.test-tmp/verify-*` directory. Omit `--package` for source-only checks; the
report records which scope ran. A source-only pass does not refresh package evidence.
Unreadable historical distribution files remain listed as unavailable; they do
not prevent check results from being retained. Package qualification requires
readable wheel and sdist hashes for the current version as well as passing checks.

Before a default report is replaced, its exact previous bytes are retained under
`.mivim/vigiles/reports/archive/runs/<run-id>/`. Failed results are retained too;
archival failure prevents replacement. To select a separate report, use a new path
such as `--report ".mivim/vigiles/reports/archive/manual-check.json"`. Existing
explicit report files and output directly at the application or Git root are
rejected before checks or workshop operations start. Do not create root backups
or versioned report copies.

If the project drive is low on space, put the dependency cache, build temporary
files, and isolated wheel environment on another volume:

```powershell
$env:UV_CACHE_DIR = "E:/codex-temp/vigiles/uv-cache"
$env:MYPY_CACHE_DIR = "E:/codex-temp/vigiles/mypy-cache"
$env:TEMP = "E:/codex-temp/vigiles/tmp"
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
$env:TMP = $env:TEMP
uv run --locked python scripts/verify.py --package --work-dir "E:/codex-temp/vigiles/checks"
```

Pytest fixtures stay in the repository's `.test-tmp` by default. Use `--test-dir`
to select another filesystem supporting symlinks or Windows junctions. Windows
exclusive publication uses native no-overwrite rename, so ordinary output does
not require hard-link support. Initial disk-space and filesystem failures are
retained in `.mivim/vigiles/reports/archive/legacy-layout/VIGILES_VERIFICATION.failed-*.json`;
a later passing run does not erase that history.

The GitHub workflow lives at `../.github/workflows/vigiles.yml`, with Windows/Linux
and Python 3.13/3.14 jobs. Local success does not imply those remote jobs have run.

Normal commands emit JSON. Exit codes: `0` success, `1` operation failure, `2`
invalid arguments, `3` audit drift or missing baseline, `130` interrupted watch.
Watch records report their own health; a bounded watch exits normally after its
requested number of scans.

Compatibility: `Bamboozletranslate`, `process_forward`, and `process_reverse` now
delegate to the shared engine. String operations no longer create/delete a shared
global ledger. Legacy `--mode/--input/--output` flags remain in
`python -m bamboozle.translate`; reverse file operations accept agent edits and new
files with the same `--dict`, `--ledger`, and optional `--strict` semantics as decode,
while stdin/stdout translation is explicitly a text-only operation. Earlier ledger
schemas are not silently upgraded. The two `.bat` launchers now call the installed
CLI. The original designs in `docs/`, `.legacy/`, and the example development loop
remain requirements to reconcile with the implementation. Their presence does not
prove implementation, and a narrower implementation does not retire their purpose.
