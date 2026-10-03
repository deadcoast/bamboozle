# MIVIM.spec.vigiles
<!--AGENTS: DISCLAIMER :: SOME THEMES AND AREAS OF DEVELOPMENT ARE EXPLICIT. BE AN ADULT. DEVELOP WHERE NECESSARY.-->
> MIVIM.spec:vigiles:["Intelligence and logistics hub", "Vigiles Daemon", "Autonomous internal system watch", "Autonomous middleware custodian"]

- *Vigiles*:
  - In ancient Rome, the Vigiles controlled the flow of the night streets, passed secret watchwords to verify identities, and deployed specialized units to precise locations. Vigiles handles the encryption, translation, and movement in the repository AGENT operations.
  - In the Roman military and Vigiles, a tessera was a small token or tablet inscribed with a secret watchword. It was passed from agent to agent to ensure secure communication, verify identity, and authorize movement through restricted zones.

---

- `spec.vigiles`
  - Operates at a foundational AGENT repository interaction level, it acts as an internal watch, forcing alignment with native structural specifications. Rather than reacting to external threats, the Vigiles profile maintains systemic health through containment, internal auditing, and structural hardening.
  - Through its Tessera Protocol, the profile serves as the system's cryptex, translator, and deployment conduit—safeguarding data through deep encryption, translating disparate agent dialects into a unified tongue, and securely routing operational forces to their target sectors while maintaining absolute structural permanence.

## Renaming the Core Functions

> Map your agent's four main responsibilities:

- Profile Enforcement ➔ Lex Custodia (Law Custody / State Guard)
  - :vigiles: actively steers data to match the repository's native specifications. It acts as the immutable standard of structural integrity, forcing all incoming and middleware traffic to conform to its own internal template.
- Security ➔ Confinement & Fire-Breaking
  - :vigiles: Agent treats anomalies, breaches, or corrupted data loops as systemic fires. It constructs digital firebreaks—isolating infected sectors, snuffing out rogue processes, and containing internal threats before they spread through the middleware.
- Counter-Intelligence ➔ Speculatores / Silent Auditing
  - :vigiles: (Historically, the Vigiles eventually integrated speculatores, or underground scouts). In your system, the agent runs silent, continuous background audits. It actively hunts for unauthorized data-mining, internal leaks, or subtle telemetry tampering within the repository itself, neutralizing threats from the inside out.
- Future-Proofing ➔ Structural Permanence
  - :vigiles: Because it feeds and enforces its own spec, the agent is self-optimizing. It continuously hardens the middleware architecture against architectural drift, ensuring that as the repository scales, the core standards remain uncorrupted and resilient against future vectors.
- The Tessera Protocol (Cryptex, Translation, & Delivery)
- The Cryptex (Obfuscation & Locking): The Vigiles act as the vault keeper. Every piece of telemetry or data packet passing through the middleware is sealed inside a dynamic cryptograph. Only agents with verified intent can read it.
  - The Translator (Semantic Alignment): The repository hosts diverse agents speaking different protocol languages. The Vigiles profile acts as the universal interpreter, translating disparate data streams into a unified, secure dialect so the system can understand itself.
  - The Delivery Mechanism (Secure Deployment): The profile provides an explicit conduit for routing authenticated payloads to designated repository sectors. Publication, recipient identity, and execution authorization are separate responsibilities with separate evidence.

## Native implementation contract — 0.2.0

Recorded 2026-10-02. The role description above supplies direction. The contracts
below define what the current implementation actually enforces. Aspirational terms
such as “absolute,” “every packet,” or “verified intent” must not be treated as
evidence of mechanisms that do not exist.

### Organization and development responsibility

Vigiles owns the Bamboozle middleware foundation: dictionary validation, reversible
translation, bounded repository file access, publication integrity, authenticated
Tessera exchange, structural audit reporting, and the CLI/MCP interfaces over those
services. Development follows review → design → implementation → verification →
specification/evidence update.

| Responsibility | Native implementation | Required evidence |
| --- | --- | --- |
| Lex Custodia | `dictionary.py`, `engine.py`, `workspace.py` | Invalid schemas, duplicate mappings, path escapes, links, ambiguous text, and excessive expansion are rejected |
| Confinement / firebreaks | `utils.py`, `service.py` | No-clobber publication, input preservation, stale-preview rejection, partial-write reporting, recoverable ledgers |
| Speculatores | `audit.py` and the audit/watch commands | Read-only drift reports against an explicitly created baseline; no source bodies in reports |
| Structural permanence | `scripts/verify.py`, installed-wheel smoke test, locked dependencies, CI | Lint/types/tests/package evidence with source hashes; separate local and remote qualification |
| Tessera cryptex / delivery | `tessera.py` | AEAD tamper/wrong-key/header/recipient rejection and exact binary restoration |
| Agent interface | `mcp.py`, `cli.py` | Real tool registration and calls; explicit preview/application token lifecycle |

### Learned invariants

1. Passing import checks did not establish application completeness. The initial
   dictionary/utility modules and README were empty, no executable package was
   configured, and both batch files named a nonexistent `cipher.py`.
2. Sequential replacements can rewrite earlier replacements and corrupt literal
   target terms. The native engine uses one pass per direction, longest-first
   matching, opaque literal protection, and a forward round-trip check.
3. Case folding is not reversible in general. Mixed case and ambiguous Unicode
   matches must fail rather than normalize away source information.
4. Translation is obfuscation, not encryption. Confidential delivery uses Tessera's
   AES-256-GCM primitive; terminology substitution never claims that protection.
5. A global `.scm_ledger.json` couples unrelated files. Native file operations use
   per-encoded-file ledgers with mapping snapshots and original/encoded digests.
6. An output and its ledger are separate publications. A post-output failure must
   report that fact and preserve the output; it must not claim a complete operation
   or delete evidence. Recovery requires an exact original/encoded pair.
7. Metadata-only cache fingerprints miss same-size, same-timestamp changes. Workshop
   lookup fingerprints include file content and the BAMb5 key. Undecodable manifests
   stop a rebuild rather than silently becoming incomplete indexes.
8. The two taxonomy shapes are real repository inputs. Both load through the same
   canonical validation and normalization logic; aliases cannot overwrite another
   canonical term.
9. Bare sibling imports, pasted hook fragments, and duplicate `_idx` definitions
   are structural defects. Package imports and explicit function inputs are the
   standard. A missing optional external pipeline is reported as unavailable.
10. Local snapshots and SHA-256 ledgers check consistency. They do not authenticate
    a party who can alter both data and metadata. Tessera authenticates key possession
    and its recipient label, not an independently verified human or agent identity.
11. Windows volumes do not all support hard links. Exclusive publication uses native
    no-overwrite rename on Windows. The installed-wheel smoke test also runs on an
    alternate volume; link-confinement fixtures need a filesystem supporting links.
12. A readable output is insufficient if its ledger exceeds the configured read
    limit. Both output and ledger limits are validated before initial publication
    or recovery. The same limit applies when restoring.
13. Disk exhaustion is a failed package qualification, not a source-code failure.
    Retain the failed record, relocate disposable build/cache state, and rerun the
    isolated package check. Source and package evidence remain separately named.

### Publication and operating boundaries

- Existing output files are preserved. Application writes stay within an explicit
  workspace and cannot modify `.mivim`, `.git`, or interpreter/cache state.
- Symlinks/junctions and alternate data streams are rejected. This is application
  confinement, not protection against an actively hostile local process racing
  ancestor directory changes. OS permissions remain part of the deployment boundary.
- Source text and mappings are bounded. Dictionaries allow at most 10,000 entries
  and 512 characters per term. Ordinary files default to 16 MiB; Tessera packets
  permit serialization overhead up to the documented 32 MiB read ceiling.
- CLI and MCP share the same conversion service. MCP application tokens are
  single-use, expire after ten minutes, and are bounded to 32 pending previews.
- Watch is an explicitly invoked, foreground, read-only monitor. The framework does
  not install itself at startup, kill processes, alter network controls, or perform
  automatic quarantine.
- Keys remain local files and are never returned in status reports. Windows key
  protection depends on the containing directory ACL. Key custody and distribution
  require an explicit deployment design.
- Direct Python translation APIs remain available for text processing. Stream
  translation does not fabricate file-integrity evidence. Earlier ledger formats
  and edited encoded files are not silently accepted by native strict file restore.

### Completion gates

`VIGILES_VERIFICATION.json` is the execution record. `VIGILES_STATUS.json` records
closed findings and actionable remaining work. A green local run qualifies only
the tested source/runtime/package paths and operating system.

The package check installs the built wheel into a fresh environment outside the
source tree, exercises byte-exact CLI round trips, and starts a real MCP stdio
child process. This is local protocol evidence; it does not establish adoption by
an external agent client. Failed qualification records remain beside the current
record, with their remediation described in the status file.

Open global gates:

| ID | Remaining work | Done criteria |
| --- | --- | --- |
| VIG-DEPLOY-01 | Native pipeline live acceptance | Native scripts and fixture qualification are supplied in 0.3.0; real candidate evaluation and operator-selected production use remain open |
| VIG-IDENTITY-01 | Recipient identities, key distribution/revocation, replay policy | Approved trust model, isolated recipients, rotation/revocation tests, and replay rejection policy |
| VIG-CI-01 | Execute remote platform matrix | Recorded passing Windows/Linux and Python 3.13/3.14 workflow runs for the submitted revision |
| VIG-RESTORE-01 | Edited-file and historical-ledger migration | Explicit migration format and conflict behavior, with no silent loss and recovery fixtures |
| VIG-HOST-01 | Optional service deployment and OS isolation | Explicit operator-selected host, least-privilege account/ACLs, bounded resource policy, start/stop/uninstall verification |

Do not mark these gates complete from unit tests, a local wheel build, an in-process
MCP test, or this specification's role language.

### VIG-DEPLOY-01 adapter preparation — 0.2.1

The next development step implements dependency inspection and the shared external
process contract in `workshop/pipeline.py`. Both workshop interfaces use it; the
native CLI exposes `workshop-check` and workshop MCP exposes `pipeline_readiness`.
Inspection checks bounded inputs, syntax, hashes, manifest fields, and matrix
references without importing scripts, creating directories, or generating caches.
`ready_for_review` establishes input shape only, not operational qualification.

Subprocess results separate missing dependencies, failure to start, nonzero exit,
timeout, and zero exit. Captured progress is retained within returned tail limits.
An external dry-run flag cannot establish absence of writes. Every started process
therefore reports that side effects are possible. The adapter does not retry,
discard partial output, or claim rollback. Timeout cleanup covers the direct
child; process-tree and resource limits require host isolation.
Interpreter paths retain virtual-environment symlinks: resolving them to a base
Python can silently lose the installed workshop dependencies.

Synthetic scripts prove argument routing, dry-run defaults, failure reporting,
preservation of interrupted copies, and explicit fixture recovery. These fixtures
are not authoritative pipeline implementations or live model evidence. The actual
`promote.py`, external `audit_matrix.py`, representative manifest/matrix, and
operator-selected workshop were the remaining dependencies at 0.2.1.
`VIGILES_PIPELINE_READINESS.json` records local discovery;
`VIGILES_STATUS.json` tracks adapter preparation separately from that open gate.

### Native pipeline authority — 0.3.0

The user's instruction to implement the pipeline here establishes the repository's
native implementation as the source authority. Historical `.legacy/dev_tools/promote.py`
is a metadata helper, not the promotion/quarantine pipeline.

- `workshop/scrape/promote.py` provides promotion, quarantine, status, and explicit
  recovery. `workshop/scrape/audit_matrix.py` provides read-only audit and explicit
  application/pruning. Both use `workshop/operations.py`.
- `workshop-init` creates a new workshop, wrapper scripts, manifests, taxonomy, and
  local key. It refuses existing contents. The local qualification target is
  `.vigiles/workshop` and contains synthetic assets only.
- Promotion requires a candidate digest and a passing validation record tied to
  that digest. The record is a local assertion, not independently authenticated
  model acceptance. Candidate files are retained.
- Quarantine verifies a separate copy before removing the owned production file.
  Removed manifest/matrix entries remain in journal before-images.
- Native writers use an OS file lock. Input/control hashes prevent stale plans
  from overwriting unrelated changes. Journals precede writes; interrupted work
  blocks another write until explicit recovery. Completion records bind to journal
  hashes. Multi-file reads are not atomic; consumers must inspect pending status.
- Recovery resumes matching before/after states and does not duplicate action
  ledger rows. Conflict or corruption requires inspection, not automatic rollback.
- Audit uses the selected workshop's taxonomy and key, fails on decode errors,
  clears stale overlap/conflict values, preserves compatibility scores/notes, and
  marks new pairs untested. Trigger overlap is not a model compatibility evaluation.
- Assets are streamed with an 8 GiB limit. Metadata/journals are limited to 16 MiB.
  Audit caps active LoRAs at 1,000 and existing matrix pairs at 50,000.

`scripts/qualify_pipeline.py` executes the native scripts with representative
manifest/matrix fixtures and a controlled post-copy ledger failure, then resumes
through the real CLI. `VIGILES_PIPELINE_QUALIFICATION.json` retains those results.
This completes the missing implementation and local file-operation qualification.
Live model evaluation, ComfyUI acceptance, cross-platform CI, and host isolation
retain separate open gates. No fixture result closes those acceptance gates.

### VIGIL3 core operations — 0.3.1

The user designated v0.3.0 as the example foundation and requested further core
development under VIGIL3, following review → plan → develop → repeat.

Review found that writer serialization did not protect consumers. An interrupted
publication could expose a new manifest with an old matrix/ledger, while MCP
cached indexes bypassed pending checks. Status could rebuild that partial index
before checking recovery state. The patch addresses this consumer boundary.

- `workshop/snapshot.py` accepts metadata only when the same completed journal set
  surrounds its capture. Journals are published before controls and retained after
  completion; an incomplete operation or changing set fails the read without retry.
  Completion records are checked against their journal hashes. Status must bind to
  a completed journal, or describe a genuinely empty initialized workshop.
- No read lock file or migration is needed. Readiness, queries, and failed reads
  do not create files. Existing v0.3.0 workspaces remain supported.
- Native manifest/matrix/ledger queries, taxonomy/trigger loaders, new operation
  planning, and lookup snapshots use this boundary. Recovery inspection remains
  available. `readable: false` prevents requested status-driven index rebuilds.
- Index creation uses captured bytes for canonicalization, cache fingerprinting,
  and encryption. Each index retains its query cipher and native matrix snapshot.
  A later rotation cannot mix a new key with an old fingerprint. Stack checks do
  not read a newer matrix after loading an older manifest.
- Native lookup includes tags as well as triggers and trained words, matching the
  native auditor. Cache writes use the shared atomic writer and strict bounded
  cache reads. Ledger responses are bounded to 1–1,000 rows.
- Evidence includes all three partial-publication paths, active-writer rejection,
  publication completing during capture, cache preservation, corrupt completion,
  status mismatch, coherent stack results, and actual installed MCP stdio recovery.

This is a consistency protocol among native participants, not an OS sandbox.
External scripts and manual/uncoordinated key, taxonomy, or metadata edits remain
outside journal serialization. A returned snapshot may become historical when
another operation starts; it does not authorize later deployment. Separate live
model, identity, remote CI, restore-migration, and host gates remain open.

### VIGIL3 bounded subprocess capture — 0.3.2

VIGIL3-OPS-02 closes the adapter's whole-stream buffering gap. Previously, returned
tails were bounded but `subprocess.run(capture_output=True)` retained the entire
output first. The new `workshop/process.py` performs fair nonblocking reads from
both pipes with fixed stdout/stderr tails.

- Retain at most 8,000/4,000 raw bytes; read at most 64 KiB per stream per loop.
  Check child state and deadlines between iterations, including when one or both
  streams continuously produce data. No complete-output buffer, spool file, or
  reader thread is used. UTF-8 replacement decoding occurs after capture.
- Report observed/retained byte counts, stream EOF, truncation, capture completeness,
  child return code, PID, and confirmed child exit. Complete draining and retained
  full output are separate properties. Incomplete counts are lower bounds.
- Child exit starts a one-second drain grace. Missing EOF after a zero child exit
  yields `capture_incomplete`/125. Pipe errors yield `capture_failed`/125. Nonzero
  child codes and timeout code 124 retain their existing meaning; capture state is
  also reported. No adapter failure is misrepresented as successful publication.
- Timeout requests one direct-child kill and bounds cleanup to two seconds.
  Pipe EOF does not remove the child execution deadline. Failed termination is
  reported with `child_exited: false` when applicable. Cancellation propagates
  after bounded owned-child cleanup. Process creation and OS scheduling are outside
  hard real-time guarantees; inherited descendant pipes are closed at the boundary.
- Side-effect, no-retry, and no-rollback contracts remain intact. Partial files are
  preserved, and capture faults after spawning still report that execution began.
- Regression evidence includes simultaneous streams exceeding 8 MiB each; exact
  byte tails/counters and less than 2 MiB traced allocation during the capture phase;
  success/failure/timeout; UTF-8 boundaries; empty streams; inherited descriptors;
  closed pipes with a live child; setup/read faults; interrupts; and kill failure.
  Script validation has a separate 16 MiB read ceiling and is excluded from the
  capture-phase memory measurement. The initial measurement included that allocation;
  the corrected test isolates capture without relaxing its 2 MiB bound.
- Installed-wheel evidence repeats noisy success/failure/timeout with 4 MiB per
  stream. Native pipeline fixture qualification and consumer recovery tests remain
  required; synthetic process evidence does not establish live model acceptance.

The supported Python baseline supplies nonblocking Windows/POSIX pipes via
[`os.set_blocking`](https://docs.python.org/3/library/os.html#os.set_blocking).
[Python's subprocess documentation](https://docs.python.org/3/library/subprocess.html#subprocess.Popen.communicate)
warns that whole-output communication buffers are unsuitable for unlimited data.
This implementation bounds adapter output storage, not the child process's total
CPU, memory, disk, or descendant lifetime. VIG-HOST-01 remains open.

---
