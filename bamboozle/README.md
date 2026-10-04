# Bamboozle

Product version: **v0.7.0**. Repository workflow: **vigiles-vandal_0.2**, named
**vandal**. Bamboozle is the original semantic encoder/cipher and reversible
translator, with separate authenticated encryption/decryption operations.
Vandal is MIVIM's proprietary agent workflow for operating and developing Bamboozle
in this repository. Agents can work through encoded file I/O or a scoped
neutral project view; Bamboozle restores the original vocabulary when returning
edited and newly generated source to the authoritative project.

Bamboozle's product APIs work independently of the repository workflow. The
application is packaged as `bamboozle`; `vigiles` remains a compatibility alias for
`bamboozle` and `python -m bamboozle`. Workflow and package versions are independent.
The manual file-conversion loop, reviewed restoration, BAMb5 token encryption,
Tessera exchange, and workshop tools remain available alongside the agent interface.

The active workflow is [.mivim/vigiles-vandal_0.2.md](.mivim/vigiles-vandal_0.2.md),
with its [repository skill](.vigiles/skills/vigiles-vandal/SKILL.md). The previous
[vigiles-vanilla_0.1 specification](.mivim/vigiles/specs/vigiles-vanilla_0.1.md)
is preserved byte-for-byte as history.
[VIGILES_STATUS.json](VIGILES_STATUS.json) holds the active development plan, core
task queue, and separate external gates. [Verification](.mivim/vigiles/reports/current/VIGILES_VERIFICATION.json)
records actual checks, test results, and source hashes. Retained evidence belongs
under [.mivim/vigiles/reports/](.mivim/vigiles/reports/README.md), with latest records
in `current/` and historical/failed records in `archive/`. Keep only active planning
and task files at the root alongside normal project and steering files.

## Connect the coding agent directly to the translator

The original `read_safe_file` / `write_safe_file` pair can now use the same
syntax-aware translators as project views. Start it with an operator-owned
`source.agent-policy.json`; this uses the existing version-2 file-policy format:

```json
{
  "schema_version": 2,
  "files": {
    "module.py": {
      "policy": "translate",
      "document": {"format": "python", "identifiers": ["task"], "modules": ["module"]}
    },
    "config.json": {
      "policy": "translate",
      "document": {"format": "json", "pointers": ["/selected"]}
    },
    "notes.md": {"policy": "translate", "document": {"format": "markdown"}}
  },
  "new_files": {
    "package": {
      "policy": "translate",
      "document": {"format": "python", "identifiers": ["task"], "modules": ["module"]}
    }
  }
}
```

With a private dictionary containing `{"task":"stage","path":"location"}`,
reading `module.py` changes the selected `task` identifier while preserving
`from pathlib import Path`, unselected strings, comments and formatting. A returned
`stage` definition or import is decoded back to `task`. Selected JSON values and
Markdown prose follow their existing adapter contracts in those same two tools.

```powershell
bamboozle --root C:\path\to\project serve-agent --dict mapping.dictionary.json --policy source.agent-policy.json
# Equivalent standalone entry point:
scm-mcp --root C:\path\to\project --dict mapping.dictionary.json --policy source.agent-policy.json
# Repository-specific Vandal configuration:
uv run --locked python scripts/vandal.py --mode agent --policy source.agent-policy.json
```

The source-agent MCP exposes five source-development tools. `read_safe_file` returns
`content`, `source_sha256`, `context_sha256`, `neutral_sha256`, `document_format`
and neutral `document_rules`. After reconnecting,
pass both hashes as `expected_sha256` and `expected_context_sha256` to
`write_safe_file`; this pins the source, dictionary, policy and document selections.
Within a running server it retains those read hashes. Changed source, policy or
translation context requires a fresh read; malformed Python is refused before
source publication. New modules may create nested package directories. Successful
writes return the new hashes and retain original/decoded bytes and private context.

Use the connected MCP tools to navigate and edit without raw-source access:

| Tool | Operation |
| --- | --- |
| `list_safe_files` | Discover files recursively within `directory` (default `.`), excluding private/protected paths and links; show neutral new-file rules. |
| `search_safe_files` | Find an exact, case-sensitive `query` in encoded documents and return neutral excerpts, locations and edit hashes. |
| `read_safe_file` | Read the entire neutral document or inclusive `start_line`/`end_line`; all three hashes still bind the full document. |
| `edit_safe_file` | Apply a batch of exact neutral replacements through the existing decoder, syntax checks and private write history. |
| `write_safe_file` | Publish a complete neutral file, including a new nested module. |

For example, search for `def stage(`, read its surrounding lines, then submit
`edits: [{"old_text":"return x + 1","new_text":"return x * 2"}]` to
`edit_safe_file` with the located `file_path` and all three read/search hashes as
`expected_sha256`, `expected_context_sha256`, `expected_neutral_sha256`. Do not
submit an excerpt to `write_safe_file`: that tool replaces the entire file.
Each edit needs a unique nonempty `old_text`, or an explicit 1-based `occurrence`
when it repeats. Empty `new_text` deletes the selected text. Every edit refers to
the same pre-edit neutral snapshot; replacements do not cascade. Overlap,
ambiguity, stale hashes, unchanged results and invalid syntax are rejected before
source or history publication. At most 128 replacements apply to one file.
Search and internal edit validation do not refresh or evict remembered agent reads.
A rejected stale edit cannot authorize a later implicit write of unseen source.

Listing/search pages use `offset`, `limit` (1–200), `next_offset`, and
`snapshot_sha256`. Pass that digest as `expected_snapshot` on later pages; changed
observations require restarting. Search coordinates are 1-based Unicode characters
with an exclusive `end_column`. `excerpt_column` locates a shortened excerpt.
An `unavailable` record makes `complete: false`; it is never a negative search
result. Search uses the selected adapter, so intentionally unselected strings and
comments retain their literal content just as in normal reads. Private decoder
diagnostics are withheld from search results. Narrow `directory` or `query` when
the 20,000-entry, 2,000-file, 64-MiB source-plus-neutral or 10,000-result limit is
reached. Pages bind observations, not an atomic snapshot of external editors.

Exact file rules take precedence over the longest matching `new_files` prefix.
Those prefix rules also govern later reads/edits of generated files. Unselected
paths outside the prefixes, explicit exclusions, dictionaries, keys and policy
files are unavailable to the agent. `copy` deliberately preserves visible content.
Fixture substitution still requires a project view. Direct writes publish one file
at a time; use views for a reviewed multi-file transaction. Unconfigured source-agent
connections retain the original whole-text behavior, so choose a policy when syntax
or selected fields must be protected. No format or symbol selection is guessed.

### Run application checks through the source agent

Add `--checks source.agent-checks.json` alongside `--policy` to enable
`list_safe_checks`, `run_safe_check` and `inspect_safe_check`. Existing connections
without `--checks` retain their five source-development tools. The operator owns
the check definitions; the agent selects a check by ID and cannot supply a command,
shell fragment or extra arguments. For a policy selecting `pipeline/`,
`workload.json`, `bench.py` and its permitted generated files:

```json
{
  "schema_version": 1,
  "checks": {
    "application": {
      "python": ["-m", "pipeline.main", "workload.json", "--capacity", "2"],
      "directory": ".",
      "python_paths": ["."],
      "timeout_seconds": 20,
      "output_bytes": 8192
    },
    "workloads": {
      "python": ["bench.py"],
      "directory": ".",
      "timeout_seconds": 30,
      "output_bytes": 8192
    }
  }
}
```

```powershell
bamboozle --root C:\path\to\project serve-agent --dict mapping.dictionary.json --policy source.agent-policy.json --checks source.agent-checks.json
# The standalone server and this repository's workflow accept the same check file:
scm-mcp --root C:\path\to\project --dict mapping.dictionary.json --policy source.agent-policy.json --checks source.agent-checks.json
uv run --locked python scripts/vandal.py --mode agent --policy source.agent-policy.json --checks source.agent-checks.json
```

The agent calls `list_safe_checks`, then passes the selected `check_id` and
`snapshot_sha256` as `expected_snapshot` to `run_safe_check`. The snapshot binds
the selected source bytes, neutral bytes, dictionary, document policy and check
configuration. New files and source edits invalidate it. Each invocation prepares
a fresh translated copy, validates selected Python import relationships, checks
the source again before execution, and runs from that copy's root. `directory`
narrows included files; it does not change their relative paths or the working
directory. Optional `python_paths` defaults to `["."]`; select `["src", "."]`
for a source layout whose packages live under `src/`. Every configured import
directory must be present in the snapshot. Third-party dependencies come from the
server's Python environment; install/configure them as the operator.

Inspect the returned `status`, `returncode`, `stdout`, `stderr`, `capture`,
`cleanup` and `source_current`. A normal nonzero exit is `failed`; deadlines are
`timed_out`. Changed source or copied inputs, incomplete pipe capture, runner
failures and unavailable diagnostics are distinct outcomes and do not count as a
pass. Captured logs use the encoder's existing matching/case rules to render source
terms in the working vocabulary, preserving already neutral names. These are
read-only diagnostics, not editable cipher documents. Fix the source through
`edit_safe_file`/`write_safe_file`, list checks again, then rerun the new snapshot.

Results survive reconnects. `inspect_safe_check({"run_id":"..."})` verifies the
retained receipt and reports `matches_current_source`; an old pass is historical
when source changes. Partial records report `incomplete` and never trigger an
automatic rerun. Original mappings, intent/results and copied workspaces remain
private under `.vigiles/cipher/checks/`. Check-created files are not imported into
source. Failed runs are retained alongside successful ones.

Configure 1–32 checks, 1–300 seconds per invocation, and 256–65,536 retained bytes
per output stream. Source plus neutral snapshots are limited to 64 MiB and reuse
the navigation file/entry limits. The runner drains both streams with fixed memory,
uses a curated environment rather than forwarding operator secrets, and assigns
Windows checks to a Job Object before project code starts. POSIX uses an owned
process group. Normal completion, timeout and interruption clean up that ownership;
cleanup failures are reported. This runs trusted project checks under the server's
OS account: the copied workspace and process ownership are **not an OS sandbox**.

`scripts/source_agent_smoke.py` exercises the complete application through actual
stdio: a failing capacity CLI check, source edits/new scheduler and package,
reconnected historical inspection, then passing application and workload checks.
The package verifier repeats it from an isolated installed wheel.

`scripts/source_agent_smoke.py --workspace NEW_DIRECTORY` exercises a complete
dependent application through actual stdio reads, disconnect/reconnect, source edits,
new modules and a new nested package. It executes the neutral and restored programs,
including workload/error coverage. The isolated-wheel qualification runs this path
using the installed product.

## Develop and return a complete encrypted project

Project exchange connects the semantic view, classical recipe and Tessera engines
through callable product operations. `view-pack` sends all files from an unchanged
exported view. `exchange-open` authenticates and reverses transport into a new,
readable receiver workspace. After development, `exchange-return` packages the
complete edited project, including new modules and missing-file information.
`view-review --return-packet` decodes that project using the owner's saved mapping,
Python/document selections and spelling context. `view-apply` imports precisely
those reviewed bytes. The owner's original neutral view remains unchanged.

| Operator action | CLI | Operator MCP |
| --- | --- | --- |
| Package fresh neutral view | `view-pack VIEW_ID -o PACKET --key KEY --recipient LABEL [--recipe RECIPE]` | `pack_project_view` |
| Authenticate and create receiver workspace | `exchange-open PACKET -o DIRECTORY --key KEY --recipient LABEL [--recipe RECIPE]` | `open_project_exchange` |
| Get receiver agent context | `exchange-context EXCHANGE_ID` | `project_exchange_context` |
| Package edited/new receiver files | `exchange-return EXCHANGE_ID -o PACKET --key KEY --recipient LABEL [--recipe RECIPE]` | `pack_project_return` |
| Decode and review return | `view-review VIEW_ID --return-packet PACKET --key KEY --recipient LABEL [--recipe RECIPE]` | `review_project_view` with the same optional arguments |
| Import reviewed return | `view-apply VIEW_ID --return-packet PACKET --key KEY --recipient LABEL --review-sha256 SHA [--recipe RECIPE]` | `apply_project_view` with the same optional arguments |

Start with the explicit view policy described under project views below. This
PowerShell sequence uses two separate local operator roots; provision keys and
recipes separately from project packets. The receiver agent works only in
`receiver-operator/work`, which contains the neutral files and public contracts.
The key, recipe and receiver journal remain outside that directory.

```powershell
$export = bamboozle view-export view-policy.json --output neutral | ConvertFrom-Json
bamboozle keygen --key transport.key
$receiver = Join-Path (Get-Location) 'receiver-operator'
New-Item -ItemType Directory -Path $receiver
Copy-Item transport.key (Join-Path $receiver 'transport.key')

bamboozle view-pack $export.view_id -o outbound.bam --key transport.key --recipient development
Copy-Item outbound.bam (Join-Path $receiver 'outbound.bam')
$opened = bamboozle --root $receiver exchange-open outbound.bam -o work --key transport.key --recipient development | ConvertFrom-Json
bamboozle --root $receiver exchange-context $opened.exchange_id

# Develop and execute the complete application in receiver-operator/work.
bamboozle --root $receiver exchange-return $opened.exchange_id -o return.bam --key transport.key --recipient development
Copy-Item (Join-Path $receiver 'return.bam') return.bam
$review = bamboozle view-review $export.view_id --return-packet return.bam --key transport.key --recipient development | ConvertFrom-Json
$review | ConvertTo-Json -Depth 30
# Inspect decoded changes, conflicts and returned terminology before application.
bamboozle view-apply $export.view_id --return-packet return.bam --key transport.key --recipient development --review-sha256 $review.review_sha256
```

To compose supplied classical methods, add `--recipe transfer.cipher-recipe.json`
to **each** pack/open/review/apply command in the corresponding direction, using
the recipe format below. Forward and return may select different recipes or keys;
each receiving operation must explicitly supply the matching recipe, key and
recipient. Recipe parameters never enter the packet; its fingerprint is bound
inside Tessera authentication. Classical transforms are reversible obfuscation;
AES-GCM supplies confidentiality and integrity. Authentication succeeds before
classical reversal or path parsing. Omit `--recipe` for authenticated transport
without classical transformation.

The bounded version-1 project frame is uncompressed and carries exact file bytes,
portable names, lengths, hashes, immutable public contracts, and the originating
view/manifest digests. It carries no original dictionary, source snapshots, private
spelling context, recipe or key. Rejects include wrong keys/recipients/recipes,
foreign returns, malformed paths, case collisions, private files, changed controls,
trailing data and oversized input. Payload plus framing and every transformed
intermediate must fit the 16 MiB Tessera bound; expanding recipes reduce the usable
project size. File limits match project views (512 source files plus controls).
Ignored build/cache directories follow the existing view snapshot contract.

Missing source files require explicit `--delete PATH` on review and apply; new
files still need the originating policy's allowlist. Source drift and changed
packets invalidate review. Existing outputs are never overwritten. Receiver
extraction saves a private journal before any project files; after interruption,
repeat `exchange-open` with the exact inputs/output and `--recover`. It refuses
changed partial files. Source import retains authenticated neutral and decoded
snapshots before changing source; inspect `view-status`, then recover with
`view-apply VIEW_ID --review-sha256 SHA --recover`, omitting packet, credentials
and deletion arguments. Its completion receipt records the returned packet hash.
Completed imports require a fresh view for another cycle. These local bindings do
not establish recipient identity, key lifecycle governance or network replay policy.

For a received exchange inside this repository, Vandal delegates context selection
with `uv run --locked python scripts/vandal.py --mode exchange --exchange-id ID`.
Outside this repository, use the product's `exchange-context`. Both select ordinary
file tools for already neutral source; do not attach `serve-agent` or operator MCP
to that agent context. This directory separation is not OS-level isolation.

`scripts/exchange_smoke.py --workspace NEW_DIRECTORY` executes the full dependent
scheduler application in separate source/receiver roots through CLI and real stdio
MCP. It develops new modules, returns an explicit deletion through a four-stage
recipe and Tessera, executes restored programs and compares workload/error results.
The installed-wheel qualifier runs the same workflow outside the checkout and
checks the implementation's import origin.

## Operate the supplied classical cipher methods

`bamboozle cipher-catalog` describes all supported algorithm variants, exact
parameter schemas, and the separate semantic and authenticated operations.
Operator MCP exposes the same data as `cipher_catalog`; Vandal's generated
`cipher_capabilities` comes from that product service.

The C codex, headers, build files and license supplied in commit
`c53f0bfded51667437fc837cd33ede37745ae9c5` remain unchanged. That commit contains
the additions; its parent `834b489` is the pre-addition checkpoint. The packaged
Python implementations derive their algorithms and registry separation from those
sources and work without a C compiler. The review records original hashes and
reproduces defects before introducing explicit lossless variants.

| Cipher ID | Pinned variant | Parameters | Byte contract |
| --- | --- | --- | --- |
| `caesar` | `ascii-v1` | `shift`: integer | ASCII letters shift modulo 26; other bytes stay literal |
| `rot13` | `ascii-v1` | `{}` | Caesar shift 13; reciprocal |
| `atbash` | `ascii-v1` | `{}` | Alphabet reflection; reciprocal |
| `affine` | `ascii-v1` | `a`, `b`: integers | `a` must be coprime to 26; normalize before multiplying |
| `vigenere` | `ascii-v1` | `key`: ASCII letters | Repeat key over letters only; retain case |
| `beaufort` | `ascii-v1` | `key`: ASCII letters | Key minus input modulo 26; reciprocal |
| `gronsfeld` | `ascii-v1` | `key`: ASCII digits | Repeat numeric shifts over letters only |
| `scytale` | `ragged-v1` | `width`: positive integer | Permute complete bytes without padding |
| `rail-fence` | `zigzag-v1` | `rails`: positive integer | Zigzag permutation with exact inverse |
| `columnar-transposition` | `ragged-v1` | `key`: printable ASCII excluding space | Stable duplicate-key ordering; no padding |
| `polybius-square` | `framed5-v1` | `{}` | Lossless case-sensitive 5×5 coordinates and literal byte escapes |

Keys have 1–128 characters. Integers are signed 64-bit values; booleans are
rejected. Every recipe pins 1–8 steps and their variants. Reversal applies each
inverse in the opposite order. Input and every intermediate output are bounded
by the workspace limit (normally 16 MiB); Polybius expansion is checked before
allocation. Transposition may produce non-UTF-8 bytes; use binary output files.

Classical transforms are **obfuscation, without authentication or confidentiality**.
They do not replace readable dictionary translation or `seal`/`open`. A wrong
classical key can produce different valid bytes. Verify against the originating
receipt for consistency, and use Tessera for authenticated transport.

Create an operator-owned `transfer.cipher-recipe.json`:

```json
{
  "schema_version": 1,
  "steps": [
    {"cipher": "vigenere", "variant": "ascii-v1", "params": {"key": "LEMON"}},
    {"cipher": "columnar-transposition", "variant": "ragged-v1", "params": {"key": "BALLOON"}},
    {"cipher": "polybius-square", "variant": "framed5-v1", "params": {}}
  ]
}
```

For an existing returned source file, review and publish the complete operation:

```powershell
$preview = bamboozle cipher-transform returned.py --recipe transfer.cipher-recipe.json -o returned.cipher | ConvertFrom-Json
$preview | Format-List
bamboozle cipher-transform returned.py --recipe transfer.cipher-recipe.json -o returned.cipher --apply --review-sha256 $preview.review_sha256
bamboozle cipher-verify returned.cipher
$reverse = bamboozle cipher-transform returned.cipher --recipe transfer.cipher-recipe.json --reverse -o recovered.py | ConvertFrom-Json
bamboozle cipher-transform returned.cipher --recipe transfer.cipher-recipe.json --reverse -o recovered.py --apply --review-sha256 $reverse.review_sha256
bamboozle cipher-verify recovered.py
```

Previews pin complete input, recipe, result and receipt bytes. They prove the
inverse before publication and do not write outputs. Application requires the
reviewed digest and unchanged inputs; existing files are never overwritten.
Receipts retain the exact recipe privately, verify both output and inverse, and
report later source/recipe drift separately. They establish consistency, not
authenticated approval. A partial output is retained: preview again with
`--recover-metadata`, then apply with that flag and the original reviewed digest.
Changed retained output or inputs require inspection and a separate destination.

MCP uses `preview_cipher_transform(source, recipe, output, reverse=False,
recover_metadata=False)`, `apply_preview(plan_id)`, and
`verify_cipher_transform(source, receipt=None)`. These tools belong to the operator.
`.cipher-recipe.json` and `.cipher-receipt.json` files are excluded from source-agent
reads/writes and project views. The public API is `bamboozle.classical.Pipeline`:
construct from the same recipe, call `transform(bytes)` or `transform(bytes,
reverse=True)`, and obtain a stable recipe digest through `fingerprint`.

The corrected formats are deliberate: ragged Scytale/Columnar preserve real
trailing `~`/`_` and original length. Polybius emits exactly three bytes per input
byte: `l11`–`l55` for lowercase coordinates, `u11`–`u55` for uppercase, and `xhh`
for lowercase hexadecimal literals outside the 25-letter alphabet, including
`j`/`J`. It rejects malformed coordinates and alternate encodings of letters.
It never guesses whether an input uses the lossy original C representation.

The complete application runner below now additionally sends edited neutral
Python through classical recipes, Tessera encryption/decryption, inverse recipes,
and reviewed source restoration through both CLI and stdio MCP. It executes the
transported neutral and restored dependency planner and capacity scheduler.

Native reference qualification is separate and preserves all C inputs:

```powershell
uv run --locked python scripts/cipher_reference.py --compiler C:/msys64/mingw64/bin/gcc.exe --work-dir .test-tmp/cipher-reference-new
```

Use an installed GCC-compatible compiler and a new work directory. On Windows,
the launcher sets a process-local compiler runtime path, disables inherited error
dialogs, preflights one source, and terminates its process tree on timeout. This
prevents repeated missing-DLL windows without changing global PATH. The retained
reference report distinguishes C-compatible vectors from corrected formats;
it does not claim sanitizer qualification of the untouched C originals.

## Develop Python programs with protected syntax

Use the explicit `python` document adapter to rename selected project identifiers
while preserving comments, docstrings, unselected strings, paths, indentation,
line endings and UTF-8 BOMs. This adapter changes original token spans, checks the
compiler's syntax/bindings, and verifies exact reverse translation before publication.
F-string expressions, closures, comprehensions, global/nonlocal declarations,
pattern bindings, selected members and keyword arguments follow the same spelling
contract. No substring splitting or inferred snake-case/camel-case mapping occurs.

Each selected identifier needs its own dictionary entry. Selection is an exact,
case-sensitive **project API spelling contract across scopes**, not type inference.
Choose names owned by the project. Module paths remain unchanged. Declare exact
project import paths whose exported symbols also translate; external members stay
literal, and selected external bindings require an explicit alias. Known external
module/object attributes and keyword names are protected. Reusing or shadowing an
external alias where selected members are involved is refused as ambiguous.

An example policy for a package whose selected dictionary maps `task` → `stage`,
`build` → `assemble`, `run` → `execute`, `weight` → `effort`, and `total` → `sum_cost`:

```json
{
  "schema_version": 2,
  "files": {
    "pipeline/__init__.py": {"policy": "copy"},
    "pipeline/model.py": {
      "policy": "translate",
      "document": {"format": "python", "identifiers": ["Task", "weight"]}
    },
    "pipeline/graph.py": {
      "policy": "translate",
      "document": {"format": "python", "identifiers": ["Task", "build", "weight", "total"], "modules": [".model"], "strings": ["build"]}
    },
    "pipeline/main.py": {
      "policy": "translate",
      "document": {"format": "python", "identifiers": ["build", "run"], "modules": [".graph"]}
    },
    "workload.json": {"policy": "copy"}
  },
  "new_files": {
    "pipeline": {"policy": "translate", "document": {"format": "python", "identifiers": ["Task", "build", "run", "weight", "total"], "modules": [".model", ".graph", ".main", ".timeline"]}}
  }
}
```

`strings` separately selects **every complete literal with that exact value**.
Use it deliberately for `__all__`, `__slots__`, reflective member names, forward
annotations, keyword dictionaries, or selected literal text. It does not infer
which identical strings have different meanings. Changed selected literals use a
canonical Python spelling; the private ledger retains original token spellings
in occurrence order for exact baseline restoration. Unselected literals remain
byte-for-byte unchanged. Selected implicit concatenations and native strings that
collide with selected neutral values are refused. Use one explicit literal token
and a distinct mapping. Dynamic computed reflection, `eval`/`exec`, namespace-dict
access and star imports are refused; replace them with explicit references before
translation. Keyword/builtin targets, magic-method targets, NFKC collisions and
unbound selected builtin calls also fail before publication.

Export using `view-export POLICY --dict MAPPING --output VIEW`. The public contract
contains neutral selections; original rules and spelling context stay private.
Develop with ordinary tools in that view, execute the whole application there,
then use `view-review` and `view-apply` with the exact reviewed digest. Project
checks verify that declared import providers exist and bind the requested members
in both neutral and decoded trees, including new files and explicit deletions.
They do not execute imports or prove dynamic types/initialization order. Run the
restored application's complete checks as well. Generated template fixture
contracts can bind to these Python consumers with the same selected symbol names.

For single files, `encode --format python --python-identifier NAME` accepts repeated
`--python-identifier`, `--python-module` and `--python-string` selections. Normal
decode, strict decode, `verify`, `recover`, `restore-edits --terms translate` and
`validate-return` retain the originating rules. Operator MCP `preview_file` takes
`document_format="python"`, `python_identifiers`, `python_modules`, and
`python_strings`; publication uses `apply_preview`. Python return diagnostics
report compiler/decoder failures and protected-source changes. Direct source-agent
tools use these same adapters when started with `--policy`. Vandal supports both
configured direct source I/O and an already prepared view.

The runnable application qualification retains original, neutral and restored
packages, a dependency-planner CLI, an agent-added capacity scheduler, and a
separate generated-workload checker:

```powershell
uv run --locked python scripts/python_smoke.py --workspace .test-tmp/python-workflow-demo
```

Choose a new workspace each time. This runs complete CLI and stdio MCP workflows,
300 scheduler executions over 100 DAGs per neutral/restored application, independent
dependency/capacity assertions, invalid inputs, exact saved-source restoration and
encrypted return composition. Inspect the retained applications under `cli/` and
`mcp/`; each final CLI accepts `python -m pipeline.main workload.json --capacity 2`
from its application directory. These local deterministic edits qualify the
implementation, not external model behavior. Python parsing is bounded to 1 million
characters, 200,000 tokens and 100,000 AST nodes; selections are limited to 512
entries per list, in addition to normal file/project byte limits. UTF-8 is required.

## Validate returned agent work

`validate-return` is a read-only operator check for edited neutral text. It binds
the retained original to its saved ledger, regenerates the encoded baseline, and
uses the originating dictionary and document rules. A newer dictionary never
reinterprets a return. The validator shares the decoder's text/Markdown/selected
JSON/Python boundaries; code fences and unselected fields/literals are not scanned as prose.

```powershell
uv run --locked bamboozle encode source.py
# Complete the agent edits in source_safe.py, preserving protected markers.
uv run --locked bamboozle validate-return source_safe.py --original source.py
# For a newly named or decrypted return, select the originating ledger explicitly:
uv run --locked bamboozle validate-return returned.py --original source.py --ledger .source_safe.py.scm_ledger.json
```

| Result | Meaning | Operator action |
| --- | --- | --- |
| `accepted` | No issue detected within the stated checks | Review the decoded diff and run applicable program tests |
| `needs_review` | Decodable text has terminology/structure findings, or analysis exceeded its limit | Resolve or deliberately accept the reported intent before reviewed application |
| `refused` | Invalid document/selected fields, damaged markers, or failed reverse translation | Correct the returned input and repeat the check |

The standalone command exits 0 for accepted, 3 for needs-review/refused, and 1 for
input/provenance errors. It grants no apply capability and writes no report or
source file. Operator MCP `validate_returned_file` accepts `source`, `original`,
optional `ledger`, `offset` and `max_findings`. It is absent from `serve-agent`.

Findings include original-domain evidence and actionable locations:

- Changed-line unmarked targets may be vocabulary or new literals. For example,
  `assemble` under an explicitly selected `fuck → assemble` mapping will decode
  to `fuck`; `__SCM_LIT_assemble__` preserves a literal `assemble`.
- Unlisted suffix forms report candidate declared stems without inventing a
  mapping. Checks cover finite English suffixes (`s`, `es`, `ed`, `ing`, `er`,
  `ers`, `est`, `ly`, and common `y`/double-consonant changes) on ASCII words and
  underscore identifiers. Explicit dictionary forms always take precedence.
- Source terms in changed neutral text, removed case/literal markers and decreased
  controlled-term counts identify possible drift. Deletions can be intentional;
  synonyms, arbitrary misspellings, multiword phrase variants and other languages
  are not inferred.
- Markdown technical spans report additions/removals/reordering. JSON reports
  changed keys, unselected values and container types separately from selected
  text. JSON locations include the exact pointer, raw token location, and line/
  column within its decoded string; other offsets are Unicode character positions.

The baseline comparison uses exact line multiplicity within each selected field
or prose region. A target on a changed line is conservatively reviewable even if
that term itself was unchanged. `accepted` does not establish intent, equivalent
meaning, model behavior or program correctness.

`restore-edits` previews include `return_validation` while retaining explicit
`--terms`/per-position decisions. The diagnostic `decoded_sha256` describes normal
saved-mapping reversal **before** manual literal decisions; the plan's output hash
describes the actual chosen restoration. Existing strict failures remain strict.

`view-review` includes diagnostics for existing and new translated files. Advisory
findings do not suppress its applicable digest; invalid translation or other
import conflicts still do. The exact review digest binds the source/view/context
bytes and selected deletions. Diagnostics do not change historical import journals
or recovery behavior. Complete the decoded review before applying that digest.

Single-file pages contain up to 500 findings (100 by default). Follow `next_offset`
using `--offset`, checking the same `validation_sha256` on every page. View review
displays at most 200 findings overall and 100 per file; retrieve remaining pages
for a particular file with:

```powershell
uv run --locked bamboozle view-review $viewId --validation-path src/generated.py --offset 100 --max-findings 100
```

The operator MCP equivalent uses `validation_path`, `validation_offset` and
`max_findings` on `review_project_view`. Check the same `review_sha256` and each
file's `validation_sha256` across pages. Totals cover all detected findings even
when display is truncated. Analysis is bounded to 524,288 combined baseline/return
characters and 8,192 translated regions, plus existing document byte/parser limits.
Exceeding analysis bounds gives `needs_review` and `analysis_complete: false`;
normal decode validity is still checked. Inspect the full diff when incomplete.

For encrypted returns, explicitly `open` the Tessera packet first, then validate
and restore the decrypted neutral file with its originating ledger. Encryption
keys, semantic dictionaries, and Vandal workflow configuration retain separate
roles. Never feed ciphertext to the semantic validator.

## Author and compile an operator lexicon

The compiler groups explicit grammatical forms, phrases and spelling variants
into concepts, then publishes an ordinary versioned dictionary. Every source
form needs its own neutral partner. It never guesses stems or collapses synonyms
into an irreversible mapping. The shipped 415-entry default, 310-entry profanity
profile and all 110 original mappings remain unchanged.

Start from the actual vocabulary, inspect it, and export a separate working copy:

```powershell
uv run --locked bamboozle dictionary-export --profile profanity -o base.json
uv run --locked bamboozle lexicon-export --dict base.json --mapping-version team-2 -o team.lexicon.json
uv run --locked bamboozle lexicon-query team.lexicon.json --query fuck --side bam --limit 20
uv run --locked bamboozle lexicon-check team.lexicon.json
```

The authoring schema has exactly `schema_version: 1`, `mapping_version`, and a
`concepts` array. Each concept has a stable `id`, optional `kind`/`note`, and one
or more `forms` containing `bam`, `boozle`, and an optional unique `label`. Export
creates deterministic IDs from the actual source terms. A concept can contain
`fuck → impulse`, `fucked → impulsed`, `fucking → impulsing` and
`f*ck → masked_impulse`; each is explicitly declared and independently reversible.
Terms follow the runtime dictionary's lowercase, disjoint-vocabulary rules.

Edit that JSON directly, or use a reviewed patch. This executable PowerShell
sequence adds two engineering terms while retaining the exported vocabulary:

```powershell
$patch = @{
    schema_version = 1
    mapping_version = 'team-2'
    remove = @()
    upsert = @(
        @{ id = 'sensor_failure'; kind = 'state'; forms = @(
            @{ bam = 'sensor fault'; boozle = 'channel anomaly'; label = 'singular' }
            @{ bam = 'sensor faults'; boozle = 'channel anomalies'; label = 'plural' }
        ) }
    )
}
$patch | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 changes.lexicon-patch.json
$edit = uv run --locked bamboozle lexicon-edit team.lexicon.json --patch changes.lexicon-patch.json -o revised.lexicon.json | ConvertFrom-Json
$edit.diff
uv run --locked bamboozle lexicon-edit team.lexicon.json --patch changes.lexicon-patch.json -o revised.lexicon.json --apply --review-sha256 $edit.review_sha256
$review = uv run --locked bamboozle lexicon-compile revised.lexicon.json --base base.json -o team-2.dictionary.json | ConvertFrom-Json
$review.changes | Format-Table
uv run --locked bamboozle lexicon-compile revised.lexicon.json --base base.json -o team-2.dictionary.json --apply --review-sha256 $review.review_sha256
uv run --locked bamboozle lexicon-verify team-2.dictionary.json
uv run --locked bamboozle encode source.py --dict team-2.dictionary.json
```

`upsert` replaces a whole concept with the specified ID or adds a new one;
`remove` names existing IDs. Removing a form means omitting it from its replacement
concept. Duplicate IDs, remove/upsert overlap and unknown removals are errors.
Edits publish a new authoring file and receipt. They never rewrite their inputs.

Compilation reviews exact additions, changed partners and removals against
`--base`. Without a base, all mappings are additions. A base requires a distinct
mapping version. Change pages are bounded to 200 entries: follow `next_offset`
using `--offset`, compare the same `review_sha256` on every page, and apply only
after inspecting all changes. Query pages can pin `lexicon_sha256` with
`--expected-sha256`; compilation accepts that pin too. `changes_sha256` covers the
complete ordered change set; pagination does not alter the reviewed artifact.
An edit's bounded diff reports `diff_complete`; inspect the full input patch and
candidate if it is false. Invalid candidates return located diagnostics and no
applicable plan. At most 100 diagnostics are displayed, with a total error count.

The compiled dictionary works with `encode`, `serve-agent --dict`, structured
conversion, and `view-export --dict`. Existing ledgers/views retain their own
mapping snapshots, so a new version cannot reinterpret an earlier agent return.
Publication refuses changed input bytes, an incorrect review digest, existing
destinations, and replay. The private `.lexicon-receipt.json` sidecar retains exact
source/base bytes, concept metadata, mapping changes and input hashes.
`lexicon-verify` reproduces the output from those snapshots and reports current
input drift separately. These are local consistency checks, not a signed identity.

If publication saves the dictionary but fails before its receipt, retain both
inputs and the published output. Re-run the same compile preview with
`--recover-metadata`, inspect its unchanged digest, then apply with that flag and
the original `--review-sha256`. Recovery refuses altered output or changed inputs.
The same explicit recovery applies to `lexicon-edit`.

Use the required `.lexicon.json`, `.lexicon-patch.json` and `.dictionary.json`
suffixes. Those files and receipts are excluded from source-agent I/O and project
views by the shared private-file guard. Keep other original-domain operator data
in `.local/` or outside the agent's selected project; filenames are not OS isolation.
Limits: 10,000 forms/patch operations, 512 characters per term, 128 per authoring
version, and the workspace's normal byte limits for input/output/provenance.

The **operator MCP** provides `export_dictionary_lexicon`,
`query_dictionary_lexicon`, `check_dictionary_lexicon`, `preview_lexicon_edit`,
`preview_lexicon_compilation`, and `verify_dictionary_compilation`. Its preview
tools return a retained `plan_id` for `apply_preview`. Agent MCP still exposes
only encoded reads and decoded writes. Neither Vandal nor the source agent owns
or silently changes the dictionary.

## Generate structurally faithful neutral fixtures

Version-3 project policies can generate JSON template fixtures directly from
production records. This replaces selected prose while keeping the surrounding
record structure, optional-field presence, identifiers, variables, role counts,
constraints and references. The original bytes stay in the authoritative project
and its private view snapshots. Fixture text is never decoded into production.
This tests a declared software contract; it does not reproduce scene semantics.

Rules are explicit operator data in a `.fixture-rules.json` file. The supported
collection is an array of JSON objects selected by `records` (use `""` for a root
array). Other data pointers are relative to each record, except `document_preserve`.
Every string value must be selected for replacement or explicitly preserved as
technical data. Unclassified strings prevent export. Preserve selectors accept
`*` for array elements; other paths are exact JSON pointers with `~0`/`~1` escapes.
Unselected keys, numbers, booleans and containers keep their values. Generated JSON
uses canonical serialization; the production file's BOM/escaping/layout stay private.

For a catalog whose Python consumer is `src/renderer.py:render_template`, this
complete rule shape validates the declared variables, role count and references:

```json
{
  "schema_version": 1,
  "records": "/templates",
  "id": "/id",
  "text": {
    "/prompt": {
      "template": "{actor_a} transfers a calibration target to {actor_b} for {duration:.1f} seconds.",
      "syntax": "format",
      "optional": false,
      "variants": {}
    }
  },
  "preserve": ["/id", "/variables/*", "/roles/*", "/role_order/*", "/links/*"],
  "document_preserve": [],
  "checks": [
    {"id": "variables", "kind": "variables", "array": "/variables", "text": "/prompt"},
    {"id": "roles", "kind": "count", "array": "/roles", "count": "/constraints/participant_count"},
    {"id": "duration", "kind": "type", "path": "/duration", "type": "number", "required": true, "minimum": 0, "maximum": 60},
    {"id": "ordered", "kind": "requires", "when": "/constraints/ordered", "equals": true, "then": ["/role_order"]}
  ],
  "references": [{"path": "/links", "many": true, "optional": false}],
  "consumers": [{"path": "src/renderer.py", "symbol": "render_template", "fields": ["/prompt", "/variables"], "purpose": "Interpolate the declared named variables and render a validated template."}],
  "literal_dependencies": [],
  "cases": [
    {"name": "zero_duration", "record": "template_017", "set": {"/duration": 0}, "remove": [], "expect": []},
    {"name": "missing_duration", "record": "template_017", "set": {}, "remove": ["/duration"], "expect": ["duration"]},
    {"name": "dangling_reference", "record": "template_017", "set": {"/links": ["missing_template"]}, "remove": [], "expect": ["@reference"]}
  ]
}
```

Use the actual fields/record IDs and Python symbols in your project. Add every
other intentionally visible string field to `preserve`/`document_preserve` or
`text`.
A `text` rule's `variants` maps record IDs to different explicit neutral templates.
Optional absent fields stay absent. Each replacement must retain the original's
placeholder multiplicity, conversions (`!s`, `!r`, `!a`), format specifications,
escaped braces and exact newline sequence. Named fields and nested named format
specifications are supported; positional, attribute and index field lookups fail
explicitly. Use `syntax: "none"` for strings the consumer never formats.

Checks are a bounded product rule language, not an implementation of JSON Schema:

| Kind | Behavior |
| --- | --- |
| `type` | Exact JSON type and presence; optional numeric `minimum`/`maximum`, string/array/object `min_length`/`max_length`, and typed `enum` choices. Booleans are not integers. |
| `variables` | Unique declared variable names match named placeholders, including nested format fields. |
| `count` | An array's length equals its declared integer count. |
| `requires` | A typed `when` value equal to `equals` requires all fields in `then`. |
| `references` | Scalar/list IDs must resolve within the selected collection. |

`literal_dependencies` must be explicitly declared. Any nonempty list blocks
generation: expose literal-dependent behavior as structured fields, or select
that source through an appropriate visible policy. Bamboozle does not guess
dependencies from prose. Consumer paths must resolve to actual Python functions
(including qualified class methods), and their declared fields must occur in the
records. The engine parses source without executing it. Descriptions are declared
by the operator; finding a function does not prove all of its behavior.

Preview without writing, then export with a version-3 policy:

```powershell
uv run --locked bamboozle fixture-preview templates.json --rules templates.fixture-rules.json
```

```json
{
  "schema_version": 3,
  "files": {
    "templates.json": {"policy": "fixture", "rules": "templates.fixture-rules.json"},
    "src/renderer.py": {"policy": "translate"}
  },
  "new_files": {"src": "translate", "tests": "copy"}
}
```

```powershell
$view = uv run --locked bamboozle view-export view-policy.json --output agent-view | ConvertFrom-Json
uv run --locked bamboozle --root agent-view fixture-validate templates.json --contract TEMPLATE_CONTRACT.json
# Develop and test code directly in agent-view, using its neutral vocabulary.
$review = uv run --locked bamboozle view-review $view.view_id | ConvertFrom-Json
$review.changes
uv run --locked bamboozle view-apply $view.view_id --review-sha256 $review.review_sha256
```

The view contains `TEMPLATE_CONTRACT.md` (field definitions, checked call paths,
and case descriptions), `TEMPLATE_CONTRACT.json` (public validation rules), and
`TEMPLATE_CASES.json` (complete neutral documents and exact expected diagnostic
codes). Case mutations operate on named records; they may set/remove fields but
cannot overlap. Generation checks every case's expected outcome before export.
An empty expected-code list means valid. Run the application against this corpus;
the fixture validator alone does not prove application behavior. To check a case
saved under another filename, use `fixture-validate case.json --contract
TEMPLATE_CONTRACT.json --fixture templates.json`.

Generated controls are immutable and never enter source import. Create separate
test files for new cases. Edited generated fixtures must still satisfy their saved
contracts before code can return; their content remains excluded from source writes.
Production-record drift blocks the return until a fresh view is prepared. Later
rule-file changes do not reinterpret a saved view. All source, consumer and rule
reads are pinned during preparation, including shared rules used by multiple files.
Existing supplied fixtures and version-1/2 policies retain their behavior.

Operator MCP exposes `preview_template_fixture` and `validate_template_fixture`;
its existing project-view tools perform generation, review, application and explicit
recovery. The source-agent MCP remains its separate source-development cipher interface.
Limits include 256 records, 64 text fields, 128 checks, 32 declared cases, 32 Python
consumers, 100 displayed diagnostics with complete counts/codes, depth 64 and
100,000 JSON nodes, plus file/view byte limits. The installed-package qualification
executes a generated multi-module catalog against valid/error cases and the original
production-shaped records; its complete synthetic scenario is in
`scripts/fixture_scenario.py`.

## Start

Run these commands from the directory containing this README and `pyproject.toml`:

```powershell
uv sync --locked --dev
uv run --locked vigiles doctor
uv run --locked vigiles validate
uv run --locked vigiles --help
```

Python 3.13 or later is required. Runtime dependencies are Cryptography, FastMCP
and markdown-it-py;
linters and tests are in the development dependency group.

All file commands operate inside an existing workspace. Its default is the current
directory. To use another project:

```powershell
uv run --locked vigiles --root "C:/path/to/project" preview "example.py"
```

Paths are relative to that root, or absolute paths contained by it. Parent escapes,
symlinks, Windows junctions, alternate data streams, and protected repository state
such as `.git` and `.venv` are rejected. Ordinary conversion output parents must
already exist; project views create their selected directory structure.
Application writes cannot replace the `.mivim` specification.

## Select the Vandal workflow context

From this application's directory:

```powershell
uv run --locked python scripts/vandal.py --mode agent
uv run --locked python scripts/vandal.py --mode operator
# After exporting a view of this repository:
uv run --locked python scripts/vandal.py --mode view --view-id $viewId
```

Each command returns one context, its working directory and `mcp_config`. The
agent configuration starts `bamboozle_source_agent` with only encoded reads and
decoded writes. The operator configuration starts `bamboozle_operator`, which can
show original-domain diffs and review/apply changes. The view context attaches no
encoding MCP: its files are already encoded and use normal agent file tools.
Keep operator and neutral-agent tool pools separate.

The helper derives absolute paths from this checkout and checks its matching
Bamboozle environment, specification and skill. It is read-only; copy its
`mcp_config` into the intended client's settings to register the connection.
It does not install or launch clients. Vandal is bound to this repository;
Bamboozle's `--root` product option still supports other project workspaces.

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
    "bamboozle_agent": {
      "command": "uv",
      "args": ["run", "--project", "C:/path/to/bamboozle/bamboozle", "--locked", "bamboozle", "--root", "C:/path/to/project", "serve-agent"]
    }
  }
}
```

The original source I/O pair remains available alongside listing, search and exact edits:

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
Without `--policy`, these direct agent tools retain whole-term text behavior.
An operator-owned policy enables Python, Markdown and selected JSON handling
directly; structured project views remain available for reviewed multi-file work.

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

## Translate structured documents

Format selection is explicit. Text mode retains the existing cipher behavior.
Markdown translates prose while protecting code fences, indented/inline code,
complete links and images, reference definitions, HTML blocks/tags, escapes and
entities. Bare URLs, email addresses and path-like literals also remain unchanged.
Emphasis can surround translated prose without breaking literal/case markers.

JSON selects exact string values by pointer and preserves the rest of the original
file, including keys, spacing and numeric spellings. For example, `/prompt` selects
the `prompt` value; `/tags/0` selects the first array string. Escape a key's `/` as
`~1` and `~` as `~0`. Select a root JSON string with the empty pointer `""`.
Wildcards are not supported; missing or nonstring selections and duplicate keys
are errors. Select every intended field explicitly.

```powershell
uv run --locked bamboozle encode guide.md --format markdown
uv run --locked bamboozle encode template.json --format json --json-pointer /prompt --json-pointer /tags/0
uv run --locked bamboozle verify template_safe.json
# After the agent edits the encoded file, review and decode using its saved rules:
uv run --locked bamboozle preview template_safe.json --reverse
uv run --locked bamboozle decode template_safe.json
# A newly named return uses the originating ledger explicitly:
uv run --locked bamboozle decode returned.json --ledger .template_safe.json.scm_ledger.json
```

Version-2 ledgers retain the dictionary, document rules and private JSON spelling
context. An unchanged encoded JSON string token returns to its exact original
spelling, including Unicode escapes; an edited selected string is serialized with
standard JSON escaping. Unselected bytes remain untouched. `decode --strict`
requires unchanged encoding and verifies exact original bytes, including BOM and
line endings. Explicit format rules must match the saved rules. Existing v1 text
ledgers continue to decode normally.

`recover --original ... --format ...` rebuilds missing metadata only when that
original exactly reproduces the encoding. Structured `restore-edits` accepts
`--terms translate`; original-bound preview/receipt checks remain intact, while
line-position decisions apply to text-format files. Literal markers express new
neutral terms intended literally. Original cipher file flags accept the same
`--format` and repeated `--json-pointer` options. Streams refuse to discard JSON
spelling context that needs a private ledger; use file-to-file conversion there.

Operator MCP `preview_file` accepts `document_format` and `json_pointers`; reverse
calls use saved context automatically. The Python API exposes
`CipherEngine.encode_document(text, FormatRules(...))` and `decode_document`.
Keep the returned `context` private and pass it when exact restoration is required.
Markdown uses CommonMark, preserves full link labels conservatively, and is limited
to 1,000,000 characters. JSON is limited to 512 selected pointers, depth 64 and
100,000 value nodes. Existing file and expansion limits also apply.

## Develop in a neutral project view

Project views support a complete multi-file development cycle. The authoritative
project retains its original source, mapping snapshot and import history. The agent
works in a separate selected tree containing encoded source, copied files, supplied
neutral fixtures and a generated terminology contract. Unselected files are omitted.

Create `view-policy.json` inside the authoritative project. For a project containing
`src/node.py`, `settings.json`, `production.json`, and a prepared
`fixtures/neutral.json`, this policy exposes only the selected content:

```json
{
  "schema_version": 1,
  "files": {
    "src/node.py": {"policy": "translate"},
    "settings.json": {"policy": "copy"},
    "production.json": {"policy": "fixture", "fixture": "fixtures/neutral.json"},
    "private-notes.txt": {"policy": "exclude"}
  },
  "new_files": {"src": "translate", "tests": "copy"}
}
```

To mix structured documents, use version 2 and attach `document` rules to each
translated file and any structured new-file directory. Version-1 policies above
remain supported unchanged:

```json
{
  "schema_version": 2,
  "files": {
    "README.md": {"policy": "translate", "document": {"format": "markdown"}},
    "template.json": {"policy": "translate", "document": {"format": "json", "pointers": ["/prompt"]}},
    "src/parser.py": {"policy": "copy"}
  },
  "new_files": {
    "docs": {"policy": "translate", "document": {"format": "markdown"}},
    "templates": {"policy": "translate", "document": {"format": "json", "pointers": ["/prompt"]}},
    "tests": "copy"
  }
}
```

| Policy | Export | Reviewed return |
| --- | --- | --- |
| `translate` | Encode the selected UTF-8 content with the chosen dictionary | Decode edits and new files with the saved mapping |
| `copy` | Exact selected bytes, visible to the agent | Apply exact edited bytes |
| `fixture` | Substitute the operator's supplied neutral file | Report changes as `fixture_ignored`; never overwrite production |
| `exclude` | Omit the named path | Refuse attempts to import it |

`files` lists exact paths; there are no globs. A new file must fall under a
`new_files` directory; the most specific matching rule wins. `.` explicitly allows
new files throughout the source root, subject to private-file and selected-path
protection. Configuration files, selected custom dictionaries, keys, cipher metadata,
`.env*`, and private state cannot be exported or overwritten through these views.

From this application's directory, set the project path and export to a new view:

```powershell
$project = 'C:/path/to/project'
$export = uv run --locked bamboozle --root $project view-export view-policy.json --output .local/views/task | ConvertFrom-Json
$viewId = $export.view_id
$agentRoot = Join-Path $project $export.view
```

The default dictionary includes the expanded profanity vocabulary. Select
`--profile profanity` for that vocabulary alone, or `--dict mapping.json` for a
custom dictionary. The exported `BAMBOOZLE_VIEW.md` lists neutral terms, marker
rules, selected files and new-file scope. It contains no inverse mapping or source
snapshots. The private manifest and content-addressed backups stay under
`.local/bamboozle/views/<view_id>/` in the authoritative project.

Start the coding agent with `$agentRoot` as its workspace and let it edit files
directly. Do not connect `serve-agent` to this already encoded tree: that interface
is for original source and would encode the view again. Run the project's applicable
checks in the view, then return to the operator context to inspect decoded changes:

```powershell
$review = uv run --locked bamboozle --root $project view-review $viewId | ConvertFrom-Json
$review.changes | Format-List
$review.conflicts | Format-Table
# After inspecting the decoded diffs and resolving every conflict:
uv run --locked bamboozle --root $project view-apply $viewId --review-sha256 $review.review_sha256
uv run --locked bamboozle --root $project view-status $viewId
```

Review is read-only. Missing editable files are conflicts until explicitly selected
with `--delete path/to/file` on **both** review and apply; repeat the flag for each
deletion. Deleted originals remain in private snapshots. Changed original source,
existing destinations for new files, changed views/contracts, and private or
out-of-scope new paths block application. Diffs are bounded; `diff_complete: false`
means the operator must inspect the retained files to review the entire change.
Only an exact matching review digest can apply. An unchanged view returns
`no_changes` without consuming it; a completed import requires a fresh export for
the next development cycle.

If publication is interrupted, `view-status` reports pending/applied/conflicting
files. Keep the view and private snapshots intact; resolve external conflicts
against the retained bytes, then explicitly resume the same import:

```powershell
uv run --locked bamboozle --root $project view-apply $viewId --review-sha256 $review.review_sha256 --recover
```

Recovery uses the saved deletion decisions and decoded outputs; omit `--delete`
when recovering. It never replaces retained intent, invents a fresh review, or
rolls back earlier files. Completion is recorded only after all outputs and view
hashes match. `complete` remains a historical receipt if files change later;
the status response separately reports current file conflicts and `view_changed`.

Operator MCP `serve` exposes the same `export_project_view`, `review_project_view`,
`apply_project_view`, and `project_view_status` methods. Decoded diffs remain on
this operator surface; the dedicated `serve-agent` exposes only its five source-development tools.

The view policy accepts UTF-8 text and preserves file paths. It does not rewrite
filenames/import paths, refactor source identifiers, generate fixtures or infer a
schema. Select Markdown/JSON adapters where appropriate; use whole-term text
translation only where it preserves the selected files' behavior, and explicit
copies or supplied fixtures elsewhere. Protected or copied content is deliberately
visible; format protection is not general secret redaction.
Views have a 512-file limit, 64 MiB aggregate snapshot limits and an 8,192-entry
traversal limit, plus the workspace's per-file limit. Runtime caches, `.git` and
`.venv` are ignored during import; links and junctions are refused. A shared lock
serializes Bamboozle view writers, but unrelated processes can still race files:
multi-file publication is recoverable, not a filesystem transaction. A neutral
working directory separates agent context; OS-level isolation requires a separate
host/account/container policy.

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

Starting with v0.4.9, original text can also contain literal marker syntax, including
incomplete or malformed examples. Marker protocol **2** quotes each original reserved
prefix as `__SCM_CASE_x_<lowercase UTF-8 hex>__`. For example, the literal prefix
`__SCM_LIT_` becomes `__SCM_CASE_x_5f5f53434d5f4c49545f__`. The payload can represent
only a reserved prefix; arbitrary hex payloads and noncanonical spelling are refused.
Reverse translation restores that prefix once, without interpreting the resulting
text as another marker. Preserve these escape tokens when editing neutral content.

To introduce a new raw marker example in agent-generated code, quote **only that new
snippet**, without needing a dictionary:

```python
from bamboozle.markers import quote_prefixes

snippet = quote_prefixes('example = "__SCM_LIT_unknown__"\n')
# Insert snippet into the neutral module, or pass it to write_safe_file.
```

Do not quote an already encoded document or a literal/case marker intended to operate
as a control token. This protocol is escaping, not encryption. It covers text and
selected Markdown/JSON regions; protected regions keep their original bytes.
New ledgers use schema **3** and view manifests schema **2**, each with
`marker_version: 2`. Historical ledgers (schemas 1/2) and manifests (schema 1) retain
protocol 1 and reject new escape tokens. Their saved mapping, document rules, original
bytes, contracts and import journals remain authoritative. Vandal reports the actual
view protocol; use a fresh export to work with literal marker examples under protocol 2.

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
CLI JSON escapes non-ASCII characters so Windows pipe code-page differences cannot
corrupt terms; JSON readers recover the original Unicode characters.

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

To compose authenticated transport with structured semantic translation, retain
the originating ledger locally and use distinct outputs at each step:

```powershell
uv run --locked bamboozle encode template.json --format json --json-pointer /prompt
uv run --locked bamboozle keygen --key transport.key
uv run --locked bamboozle seal template_safe.json --key transport.key --recipient reviewer -o packet.bam
uv run --locked bamboozle open packet.bam --key transport.key --recipient reviewer -o returned.json
uv run --locked bamboozle decode returned.json --ledger .template_safe.json.scm_ledger.json --strict
```

This sequence proves the unchanged payload round trip. If an agent changes the
decrypted document, preview its reverse conversion and use ordinary decode rather
than strict integrity restoration. Encryption and semantic translation remain
explicit operations with different outputs, keys and responsibilities.

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
