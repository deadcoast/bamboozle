# Vigiles development evidence

All paths below are relative to this directory. The application root keeps only
active planning/task files alongside ordinary project files. Its
`VIGILES_STATUS.json` points here; it is not the historical report database.

| Latest record | Scope |
| --- | --- |
| [Verification](current/VIGILES_VERIFICATION.json) | Check results and source digests; `package_requested` distinguishes source checks from package qualification |
| [Pipeline qualification](current/VIGILES_PIPELINE_QUALIFICATION.json) | Synthetic native operation and recovery evidence |
| [Plan qualification](current/VIGILES_PLAN_QUALIFICATION.json) | Exact-plan CLI application and recovery evidence |
| [Inventory qualification](current/VIGILES_INVENTORY_QUALIFICATION.json) | Bounded discovery and damaged-record visibility |
| [Pipeline readiness](current/VIGILES_PIPELINE_READINESS.json) | Local dependency inspection |
| [Capture qualification](current/VIGILES_CAPTURE_QUALIFICATION.json) | Bounded subprocess capture evidence |

`current/` means latest retained record of that type, not qualification of every
subsequent edit. Check creation time, source digests, scope, and result. Old release
records do not prove new code or live deployment.

The verifier and three qualification scripts write to `current/` by default. Before
replacement they preserve the previous exact bytes in
`archive/runs/<UTC-timestamp-unique-id>/`. Failed records receive the same retention
as passing ones. If archival fails, the current record is not replaced. An explicit
`--report` selects a new JSON file and never overwrites existing evidence; outputs
directly at the application or Git root are rejected before work starts.

`archive/legacy-layout/` contains all 42 original root JSON records, including the
full historical status. [INDEX.json](INDEX.json) records their original root names,
archive paths, sizes and SHA-256 hashes. Original bytes are unchanged, including
embedded historical filenames, source hashes and completion claims. Paths inside
those records (including copies in `current/`) retain their original application-root
meaning; use the index to locate former root report names. Do not rewrite them or
interpret them relative to this new directory. The initial current copies let the
archive remain immutable when current evidence is refreshed.
The local `.gitattributes` disables Git newline conversion for archived files so
those byte hashes remain valid across Windows and Linux checkouts.

Keep completed planning snapshots and future historical reports in `archive/`.
Keep active status concise. Do not create root report backups, versioned status
dumps, or additional handoff files at the root. Logs and package/fixture artifacts
stay at their recorded locations; this relocation does not discard them.

`.mivim/` is retained specification/development evidence. The separate `.vigiles/`
directory is ignored local runtime/fixture storage and may contain keys. Do not
copy runtime keys or real model assets into retained reports.
