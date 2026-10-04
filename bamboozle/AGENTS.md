# Bamboozle / MIVIM Vigiles Vandal

## Scope and authority

This directory is the Python application root. Run application and verification
commands here; the Git repository and CI workflow are one level above.

Before changing implementation, read:

- `.mivim/vigiles-vandal_0.2.md`, `.vigiles/skills/vigiles-vandal/SKILL.md`,
  and the original designs in `docs/`, `.legacy/`, and
  `../example_project/dev_tools/development_loop.md` for the intended system.
  Versioned contracts record implemented behavior; they do not supersede unmet
  design requirements or authorize narrowing the user's product.
- `VIGILES_STATUS.json` for the current stage, next step, evidence, and open gates.
- `README.md` for supported commands and operational boundaries.
- `pyproject.toml` and `uv.lock` for packaging, Python, and dependency constraints.

Check the working tree and preserve existing edits. Follow review → design →
implementation → verification → specification/evidence update. When asked to
continue development, start with the current `next_step` rather than repeating a
completed stage. Keep work within the user's requested scope.
The user's current direction takes precedence over assistant-written task queues.
Bamboozle is the original product: its encoder/cipher and reversible translator
define the development purpose. Vigiles is the agent workflow built to operate
those features and support their integrity. Develop usable Bamboozle capabilities;
do not turn the product into a prerequisite or accessory of the agent framework. The
original SCM contract includes `read_safe_file` (encode before returning content)
and `write_safe_file` (decode the agent's content before saving local source).
The active workflow is `vigiles-vandal_0.2`, named `vandal`, for this repository
only. Its version is independent of the Bamboozle package. The previous
`vigiles-vanilla_0.1` specification is retained under `.mivim/vigiles/specs/`.
Keep product engines separate from workflow configuration: Vandal routes existing
CLI/MCP capabilities and never supplies a replacement encoder or cipher.
Manual file conversion alone does not establish that agent integration. Keep the
dedicated `serve-agent` surface distinct from operator tools that display raw diffs.
Preserve existing functionality while restoring the core source → neutral agent
work → edited/new source return pipeline. Integrate review, recovery, audit,
workshop, and exchange capabilities around that operational pipeline; do not
discard them or make them prerequisites that prevent normal agent development.
Before selecting an external acceptance gate as the next development task, review
the core workflow against the system designs. Keep a concrete source-development
queue in status. External deployment inputs do not block independent core work;
do not infer whole-project completeness from a completed implementation stage.

## Planning and evidence placement

The application and Git roots are not report directories. Keep only active
planning/task files there, such as the compact `VIGILES_STATUS.json` or a current
TODO/feature plan, alongside normal source/configuration files and steering docs.
Do not accumulate completed plans, qualification dumps, versioned status copies,
failed-run reports, or handoff bundles at either root.

| Content | Application-relative location |
| --- | --- |
| Active stage, next task, open gates, evidence pointers | `VIGILES_STATUS.json` |
| Vandal authority and skill | `.mivim/vigiles-vandal_0.2.md`, `.vigiles/skills/vigiles-vandal/SKILL.md` |
| Latest development evidence by report type | `.mivim/vigiles/reports/current/` |
| Completed/superseded/failed records and status snapshots | `.mivim/vigiles/reports/archive/` |
| Original root-record relocation map and hashes | `.mivim/vigiles/reports/INDEX.json` |
| Disposable test/build scratch and synthetic runtime workshops | `.test-tmp/`, `.vigiles/` outside tracked `skills/`, or explicit scratch paths |

Preservation means retaining records in the archive, not keeping them at the root.
Archive exact bytes before replacing current evidence; retain failures and logs.
Keep root status concise: completed findings, detailed results and history belong
in reports, with pointers from status. Historical embedded paths are interpreted
from their original application root; use `INDEX.json` to resolve relocated report
names without rewriting their contents or hashes. See the reports `README.md`.
Apply this repository's layout even when an unrelated project previously requested
a root-level handoff. Do not generate extra tracking documents without a task need.

## Code map

| Area | Source |
| --- | --- |
| Dictionary validation and reversible translation | `src/bamboozle/dictionary.py`, `engine.py`, `service.py` |
| Shared Markdown/selected-JSON adapters | `src/bamboozle/formats.py` |
| Explicit Python token/span translation and project import checks | `src/bamboozle/python_source.py`, `python_project.py` |
| Generated neutral fixtures and processing contracts | `src/bamboozle/fixture_rules.py`, `fixtures.py` |
| Repository-only workflow context/MCP configuration | `scripts/vandal.py` |
| Public encoder/cipher API and agent-mediated source I/O | `src/bamboozle/cipher.py`, `agent_files.py`, `scm_mcp.py` |
| Confined file access and publication | `src/bamboozle/workspace.py`, `utils.py` |
| Tessera authenticated exchange and repository audits | `src/bamboozle/tessera.py`, `audit.py` |
| Main CLI and MCP tools | `src/bamboozle/cli.py`, `mcp.py` |
| Edited source restoration and historical migration | `src/bamboozle/restoration.py`, `migration.py` |
| Operator returned-text terminology and structure validation | `src/bamboozle/return_validation.py` |
| Scoped project views and reviewed multi-file return | `src/bamboozle/project_views.py`, `view_import.py` |
| Native workshop initialization and journaled operations | `src/workshop/bootstrap.py`, `operations.py` |
| Read-only recovery conflict diagnostics | `src/workshop/recovery.py`, shared validation in `operations.py` |
| Durable plans, discovery, and consumer consistency | `src/workshop/plans.py`, `inventory.py`, `snapshot.py` |
| External pipeline adapter and bounded output capture | `src/workshop/pipeline.py`, `process.py` |
| Native script entry points | `src/workshop/scrape/promote.py`, `audit_matrix.py` |
| Workshop MCP interfaces | `src/workshop/mcp/workshop_mcp.py`, `src/workshop/scrape/bamb5.py` |
| Taxonomy and lookup | `src/workshop/scrape/alias_pass.py`, `lookup.py` |

Tests live under `src/bamboozle/tests`, `src/workshop/tests`,
`src/workshop/scrape/tests`, and `src/workshop/mcp/tests`. Use package-qualified
imports; do not restore bare sibling imports or ad hoc `sys.path` modifications.
Keep CLI/MCP interfaces thin and share behavior through the service modules.
For workshop tool changes, update and exercise both MCP interfaces.

## Environment and verification

Use Python 3.13+ and the existing uv environment. Preserve the lockfile and avoid
unrelated dependency upgrades or replacing the environment.

```powershell
uv sync --locked --dev
uv run --locked python -m ruff check .
uv run --locked python -m ruff format --check src scripts
uv run --locked python -m mypy src scripts
uv run --locked python -m pyright src scripts
```

Run focused pytest cases while developing. Use a fresh repository-local
`--basetemp` under `.test-tmp`; pytest can clean a reused base directory. Windows
junction/link fixtures need a filesystem that supports them.

For feature and release qualification:

```powershell
uv run --locked python scripts/verify.py --package
```

The verifier runs lint, formatting, types, tests, compilation, wheel/sdist builds,
and an isolated installed-wheel smoke test. Read its actual result and logs.
It writes `.mivim/vigiles/reports/current/VIGILES_VERIFICATION.json` by default and
automatically archives the previous report under `archive/runs/`. Qualifiers use
the same storage policy. An explicit `--report` must be a new nested JSON path;
outputs directly in the application/Git root and existing explicit files are
refused before checks or workshop operations start. Keep failed and superseded
evidence, including its logs and relevant artifacts.

If disk or temporary-directory problems occur, use `UV_CACHE_DIR`,
`MYPY_CACHE_DIR`, `TEMP`/`TMP`, and the verifier's `--work-dir` option as documented
in the README. Do not delete retained evidence or replace an existing cache
junction to work around a failure.

For documentation-only changes, verify content, referenced paths, and whitespace.
Do not regenerate release evidence or claim a fresh runtime qualification.

## Workshop and MCP conventions

- Select workshops with `VIGILES_WORKSHOP_ROOT` or the native CLI's `--root`.
  `VIGILES_WORKSHOP_PYTHON` selects the adapter interpreter; configuration lives in
  `src/workshop/config.py`. Never hardcode a developer's workshop path.
- Use synthetic fixtures from `workshop.examples`. The qualifiers
  `scripts/qualify_pipeline.py`, `scripts/qualify_plans.py`,
  `scripts/qualify_inventory.py`, and `scripts/qualify_recovery.py` require new or
  empty workshop paths. Omit `--report`
  for managed current/archive storage, or select a new explicit nested report path.
  Existing fixtures may intentionally contain corruption; preserve them.
- `python -m workshop.mcp.workshop_mcp` starts the primary stdio server.
  `python -m workshop.scrape.bamb5` starts the translation CLI. To test its MCP
  server, launch `python -c "from workshop.scrape.bamb5 import mcp; mcp.run()"`.
  Installed smoke tests must import the wheel from outside the source checkout.
- Keep new native options explicit. External scripts must not silently receive
  unsupported native flags.

## Invariants to preserve

- Preserve the full encode → agent edits or generates code → decode loop through
  the normal CLI, MCP, compatibility API, and Windows launchers. Normal decode
  reverses unmarked target terms and preserves protected literals by the documented
  terminology contract; it does not require an unchanged file or original source.
  `decode --strict` and `verify` retain exact round-trip integrity checks. The
  optional `restore-edits` review/receipt workflow and explicit ledger migration
  remain available. Never silently override a saved mapping or replace source files.

- Preserve original bytes, no-clobber publication, bounded reads, strict JSON
  parsing, and workspace path confinement. Use the shared file utilities.
  Application writes cannot overwrite `.mivim`; deliberate specification edits and
  development report publication through `bamboozle.evidence` are scoped development
  operations. Do not weaken runtime workspace protection to implement reporting.
- Agent `write_safe_file` is the explicit source-edit interface: it may update a
  previously read source after validating its hash and retaining its prior bytes
  in private history. Existing ordinary conversion outputs remain no-clobber.
  Agent reads return encoded content; writes do not return decoded source. Never
  expose configured inverse dictionaries or private history through agent tools.
- Keep the original 110 dictionary mappings stable. The default profile merges
  them with the versioned profanity vocabulary; custom dictionaries and saved
  ledgers remain independent. Every added spelling needs its own reversible target.
  Preserve literal and case markers through agent and reviewed-restoration paths.
  Operator scans may display original terms; do not add them to `serve-agent`.
  Marker protocol 2 quotes literal reserved prefixes once; decode never rescans
  restored text. New ledger schema 3/view manifest schema 2 pin that protocol.
  Historical schemas retain protocol 1 and their exact saved contracts/journals.
- Project views export only explicitly selected copy/translate/fixture files.
  Keep mapping snapshots and original bytes in authoritative private storage,
  outside the neutral agent view. Bind import to reviewed source/view bytes;
  require explicit deletion selection and allowlisted new-file destinations.
  Publish the retained import before source changes and a completion receipt only
  after all final checks. Partial imports require explicit recovery, not rollback.
- Preserve saved document rules alongside dictionary snapshots. Format selection
  is explicit: Markdown protects technical syntax; JSON selects exact string
  pointers and retains original spelling context privately. Never translate keys
  or unselected fields by implicitly falling back to whole-file text processing.
  Direct source-agent tools remain text-mode; route structured multi-file agent
  work through version-2 views. Never connect an encoding MCP to an encoded view.
  Python rules select exact identifiers, project import paths and explicit literal
  values. Preserve unselected syntax, private spelling context and historical
  formats. Validate both sides of declared imports and execute whole dependent
  applications; standalone token assertions cannot establish application behavior.
- Lexicon authoring and compilation are operator operations. Explicit concept
  forms compile into the existing Dictionary type; do not infer morphology or
  merge reverse partners. Publish new versions under reviewed hashes, retain
  source/base provenance and never reinterpret saved ledgers/views. Keep lexicon,
  patch, compiled dictionary and receipt suffixes excluded by the shared agent/view
  private-file guard. Query/diff pagination must not change the reviewed artifact.
- Generated fixture rules are private operator inputs. Classify all strings,
  preserve declared placeholder/structural invariants and validate expected cases
  before v3 view export. Bind every consumer to selected Python source and pin
  repeated rule/source reads to the same bytes. Keep generated public controls
  immutable; validate edited fixtures and unchanged production records on return.
  Fixture data/control files never overwrite production templates during import.
- Return diagnostics share document regions and saved dictionary/format context.
  They are operator evidence, never guessed reverse mappings or automatic intent
  decisions. Preserve strict marker/decoder failures and manual restoration choices.
  Page findings under unchanged validation/review digests; incomplete analysis is
  not accepted. Keep diagnostics out of source-agent tools and old journal bytes.
- Publish journals before asset/control changes and bind completion to journal
  bytes. Retain partial writes and report recovery requirements truthfully.
  Recovery is explicit; do not add automatic rollback, retry, or history deletion.
- Prepared application requires the reviewed ID/digest and fresh input, control,
  and asset checks. Apply the saved bytes unchanged. Reject consumed/cancelled
  plans and retain cancellation evidence.
- Native consumers must use the snapshot boundary. Do not expose partial
  publication or rebuild an index from pending state.
- Recovery diagnostics may inspect partial state only to produce bounded, read-only
  evidence. Share retained-journal validation with recovery, report all relevant
  conflicts, withhold readiness on incomplete/changing observations, and keep
  completed journals distinct from the current files of later operations.
- Inventory is read-only and checks metadata, not model hashes or global apply
  readiness. Preserve bounded responses/reads, cursor invalidation, and visibility
  of corrupt, unavailable, and changing records. Inspection/apply retains full
  validation.
- Keep subprocess output capture bounded while draining both streams. Preserve
  partial files and distinguish child exit from complete capture. Direct-child
  cleanup does not establish process-tree isolation.
- Translation is obfuscation, not encryption. Local digests establish consistency,
  not authenticated approval. Do not include keys or real model assets in source,
  reports, or package artifacts.

## Handoff and completion

For completed development stages, update the specification, README, and
`VIGILES_STATUS.json` with actual evidence and remaining work. For a versioned
release, keep `pyproject.toml`, `src/bamboozle/__init__.py`, and `uv.lock` aligned.
Do not invent test counts, change source hashes to conceal edits, or treat old
reports as qualification of new code.

Separate source, installed-package, synthetic fixture, remote CI, and live
acceptance evidence. The CI workflow is `../.github/workflows/vigiles.yml`.
Local success does not close live deployment, remote CI, identity/key governance,
restore migration, or host/process-tree-isolation gates. Consult current status
before making any completion claim.
