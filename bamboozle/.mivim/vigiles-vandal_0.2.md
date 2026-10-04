# MIVIM.spec: vigiles-vandal_0.2

Workflow name: **vandal**. Workflow version: **0.2**. Scope: **this MIVIM
Bamboozle repository only**. This supersedes the operational workflow named
`vigiles-vanilla_0.1`; its exact previous specification remains in
[specs/vigiles-vanilla_0.1.md](vigiles/specs/vigiles-vanilla_0.1.md).

## Product and workflow responsibilities

Bamboozle is the original application. Its semantic encoder/cipher, reversible
translator, dictionaries, format adapters and project views are the foundation.
It also supplies authenticated file exchange; the retained BAMb5 token cipher is
a separate encryption mechanism. Vandal operates these capabilities through a
repository-specific CLI agent workflow. Neither system is a replacement for the
other. Product and workflow versions advance independently.

| Owner | Responsibility | Interface |
| --- | --- | --- |
| Bamboozle semantic engine | Readable reversible vocabulary, literal/case protection | `CipherEngine`, `DocumentCodec`, `encode` / `decode` |
| Bamboozle document adapters | Selected prose/JSON values with preserved technical structure | Explicit document rules in CLI, ledger and view policy |
| Bamboozle project service | Selected neutral view and reviewed, recoverable source return | `view-export`, `view-review`, `view-apply`, `view-status` |
| Bamboozle project exchange | Complete neutral project transport, receiver context and authenticated return | `view-pack`, `exchange-open`, `exchange-context`, `exchange-return`, returned-packet view review/apply |
| Bamboozle return validator | Saved-context terminology and protected-structure diagnostics | `validate-return`, `validate_returned_file`, restoration/view review |
| Bamboozle structural profiles | Declared repository rules, operator findings and prospective source publication guards | `structure-check`, `check_repository_structure`, audit, source write/view return |
| Bamboozle source agent I/O | Encode reads and decode writes with original backups | Dedicated `serve-agent` / `scm-mcp` |
| Bamboozle peer custody | Local identity bindings, key retirement/revocation and durable replay admission | `identity-init`, `identity-status`, `recipient-*`, `delivery-status`, managed `--peer` file/project operations |
| Bamboozle Tessera | Authenticated binary encryption/decryption, AES-256-GCM | Operator `keygen`, `seal`, `open` |
| Bamboozle classical methods | Explicit reversible byte transforms and composed recipes | `classical.Pipeline`, operator `cipher-transform` / `cipher-verify` |
| BAMb5 | Deterministic authenticated term tokens, AES-SIV | `workshop.scrape.bamb5` `enc` / `dec` and selected-key JSON operations |
| Vandal | Choose context, scope work, route tools, develop and verify this repository | Repository skill and `scripts/vandal.py` |
| Retained supporting services | Audit, plans, inventory, migration, recovery and workshop integration | Existing explicit operator interfaces |

The product modules do not import Vandal or require its specification to translate
another project's files. The Vandal helper is bound to this checkout and delegates
to product APIs; it contains no second implementation of translation or encryption.
`vigiles` remains a compatibility alias for the product CLI, not a second product
or the implementation of this workflow.

## Context and operator boundaries

Product 0.7.0 adds the `exchange` context for an authenticated received project.
Vandal selects it with `--mode exchange --exchange-id ID` and delegates to the
product's retained receiver context. It has ordinary file tools, no source encoder
or operator MCP connection. Source-owner mapping/spelling snapshots never enter
the receiver workspace. The operator provisions keys/recipes outside agent roots.

Project exchanges use a bounded uncompressed version-1 frame, immutable generated
controls, portable names and exact byte lengths/digests. Optional classical recipes
transform the full frame; mandatory Tessera authenticates its version and recipe
fingerprint before reversal/parsing. Outbound views must match their exported
snapshot. Returned frames bind to view ID and private manifest digest and enter
the existing decoded review/import directly. Preserve selected Python syntax,
format context, fixtures, allowed new files, explicit deletion decisions and source
drift checks. A changed authenticated packet invalidates the review digest.

Receiver extraction journals precede file creation and require explicit recovery
with matching packet/destination. Source import journals retain authenticated
neutral snapshots and decoded output before writes; recovery needs that retained
journal and reviewed digest, not the original transport credentials. Historical
local-view journal/receipt bytes remain valid; only exchange plans/receipts include
`exchange_sha256`. `view-status` reports their retained return source separately
from the untouched local view. Execute the entire dependent application in both
receiver and restored source roots through CLI, actual stdio MCP and installed
package qualification. Local authentication is not recipient lifecycle governance.

The repository skill is
[`.vigiles/skills/vigiles-vandal/SKILL.md`](../.vigiles/skills/vigiles-vandal/SKILL.md).
Read it with `AGENTS.md` and the current task in `VIGILES_STATUS.json`. Generate
the appropriate context with `uv run --locked python scripts/vandal.py --mode ...`:

1. **Original-source agent** (`agent`): `list_safe_files`, `search_safe_files`,
   `read_safe_file`, `edit_safe_file` and `write_safe_file`.
   These use whole-term text rules unless an operator supplies `--policy` with a
   private `.agent-policy.json`. Then source reads/search/edits use the shared explicit
   Python, Markdown, JSON, YAML or text adapters. Reads return neutral content; writes return
   hashes/status rather than original-domain content. Existing writes require a
   prior source hash and, for configured writes after reconnecting, the read's
   context hash. That context binds dictionary, policy, format and source. The
   configured dictionary, keys, rules and spelling history remain outside these
   tools. New package directories are created after validation; malformed Python
   and stale contexts are refused before writing source. Unconfigured text mode
   does not provide syntax protection. No format or selection is inferred.
   Discover through the scoped listing, search the encoded vocabulary and use
   ranged reads when appropriate. Keep all three source/context/neutral hashes
   for exact edits; never send a partial read as a whole-file replacement. Edit
   batches select non-overlapping spans in one original neutral snapshot, require
   explicit occurrence for repeated anchors, and publish through the original
   decoder/history path. Read document rules and generated-directory rules expose
   neutral selections only. Page listings/searches under their snapshot digest;
   unavailable records mean incomplete search. No raw decoder errors enter search
   results. Existing original filenames remain visible. This is single-file
   development; multi-file reviewed publication remains the view workflow.
   With operator-owned `--checks ...agent-checks.json` and an explicit policy,
   the product adds `list_safe_checks`, `run_safe_check`, `inspect_safe_check`.
   List the current snapshot, run a named check, correct source using the existing
   decoder, then list/run again. Commands, import paths, scope and limits are fixed
   by the operator. Each run uses a fresh neutral copy, checks source/configuration
   drift and retains failure/success receipts. Inspect historical results after
   reconnects and compare `matches_current_source`. Diagnostics reuse the semantic
   encoder's matches without re-encoding already neutral names. The product owns
   process/output bounds and Windows Job Object/POSIX process-group cleanup;
   Vandal supplies configuration/routing only. No OS sandbox or hostile-code
   isolation is implied. Check output files never return to source automatically.
2. **Neutral project agent** (`view --view-id ID`): ordinary file tools inside the
   exported tree, with no encoder-backed or operator MCP attached. Read the generated
   view contract. The tree is already encoded; double encoding is an error. This
   supports mixed formats, fixtures and new dependent files under saved policies.
3. **Operator** (`operator`): original-domain diffs, vocabulary/format selection,
   export, exact reviewed application, retained-state inspection, explicit recovery
   and encryption/decryption. Operator MCP is a separate configuration and is not
   part of the neutral agent's tool pool.

The helper emits absolute paths, the independent product/workflow identities and
one selected configuration. It is read-only and never registers, launches or
changes a client. A generated connection is not proof of client configuration or
OS isolation. Real stdio qualification proves the exposed tool sets and calls.

## Operational source cycle

Review → design → implement → verify → update specification/evidence. The user's
current product direction takes precedence over inherited task queues. Choose
complete source-to-return capabilities and retain prior useful work. Supporting
framework, workshop or deployment tasks cannot displace unfinished core features.

For multi-file work, classify exact files as copy, translate, supplied fixture or
exclude; omit unselected content. A version-2 policy adds document rules to
translated files and permitted new-file directories. Keep originals, complete
reverse mappings and spelling context in authoritative private storage outside
the view. The agent gets only selected prepared content and its neutral contract.

Let the agent complete edits/new files and run applicable view checks. Review the
decoded diff in operator context. Missing files require explicit deletion selection
on review and application; new files require approved directories and absent source
destinations. Apply only the exact reviewed digest with fresh source/view/manifest
checks. Retain intent and decoded bytes before writes, then verify all final hashes
before recording completion. Execute/check the restored original-vocabulary result.

On interruption, preserve actual partial state. Inspect the existing journal and
resume explicitly with the same digest; do not regenerate intent, delete evidence
or roll back automatically. Completed receipts remain historical facts while
current file drift is reported separately. Cooperating writers share a view lock;
the filesystem is not a multi-file transaction against unrelated writers.

## Structured translation contract — product 0.4.5

- `text` retains established whole-term matching and literal/case markers. Existing
  110 original mappings, expanded profanity profiles, v1 ledgers, v1 project policies,
  original cipher flags and direct agent file tools remain supported.
- `markdown` uses CommonMark parsing to protect fenced/indented code, inline code,
  full links/images/reference definitions, HTML blocks/tags, entities and escapes.
  Bare addresses, URLs and path-like literals are also protected. Translate prose,
  including emphasis, while keeping cipher markers intact. Full link labels are
  preserved conservatively. This is not a source-code refactoring or HTML adapter.
- `json` requires exact RFC-6901-style pointers to existing string values. Keys,
  unselected values, whitespace, numeric spellings and surrounding syntax remain
  byte-for-byte intact. Escape `~` as `~0` and `/` as `~1`; use explicit array indices.
  Missing/nonstring values and duplicate keys fail before publication. There is no
  wildcard expansion, inferred field selection or automatic schema migration.
- Changed selected JSON strings are serialized as valid JSON. Private context
  restores exact original token spellings when their encoded tokens remain
  unchanged. A changed selected string may get standard JSON escaping; unselected
  bytes stay untouched. Do not expose that original spelling context to the agent.
- New v2 ledgers retain document rules/context with the dictionary snapshot. Normal
  and strict decode, verify, missing-ledger recovery, and explicit structured
  `restore-edits --terms translate` use the same adapter. Explicit mismatched rules
  fail. Old line-based intent decisions remain available for text-format restoration.
- CLI and operator MCP select the same rules. Version-2 project policies retain
  them for edited/new files and recovery. Stream translation refuses to discard
  required JSON spelling context; use file-to-file conversion when a ledger is needed.
- Keep reads and expansion bounded. Markdown parsing is limited to 1,000,000
  characters; JSON to 512 selected pointers, depth 64 and 100,000 value nodes, plus
  existing file/view limits. Unsupported or incompatible syntax fails explicitly.

## Recursive JSON string values

The original `.legacy/designlog:897-899` values-only requirement is implemented
by the shared product document adapter. Explicit rules are
`{"format":"json","values_only":true}`; CLI/compatibility use
`--json-values-only`, operator MCP uses `json_values_only: true`. This selects
every nested string value and permits new returned/generated string values while
leaving keys and nonstring tokens untouched by translation. Exact-pointer rules
and historical saved contexts retain their behavior. See `docs/json-values.md`.

Keep private scalar spelling context with saved dictionaries/rules. Unchanged
tokens restore their original spelling at their pointers. For moved recursive
tokens, use retained spelling only when all saved occurrences agree; conflicting
spellings use ordinary JSON rendering. Never guess node identity. Return review
retains key/presence/type/nonstring evidence while excluding selected prose values.
The scanner bounds nodes/depth plus 10,000 recursive values, 2,048 characters per
pointer and 1,000,000 total selected pointer characters. Invalid controls, grammar,
unrepresentable selected Unicode and exceeded bounds refuse before publication.

Source-agent reads/edits/writes, project views, restoration and import recovery
share these rules. Vandal routes existing product capabilities. Qualify a complete
nested configuration/data application through CLI and operator/source-agent stdio,
including inserted/moved values, generated configuration, dependent code and
interrupted import. `scripts/json_values_smoke.py` runs those flows from source
and the installed wheel.

## Explicit text literal protection

The original `.legacy/designlog:878` path/URL requirement is implemented in the
product's `text_literals.py` and shared `DocumentCodec`. Opt in with
`{"format":"text","protect":["paths","urls"]}`, CLI repeated `--protect` under
`--format text`, or operator MCP `preview_file.protect`. Either class can be
selected alone. Historical text rules and other document adapters are unchanged.
See `docs/text-literals.md` for the version-1 lexical grammar and executable flows.

Preserve recognized literal bytes and private source/encoded span context across
ledgers, restoration, source-agent read/edit/write, new-file rules and project
view import/recovery. Bounds are 1,000,000 characters, 10,000 spans and existing
byte limits. Exact inverse checks refuse ambiguous dictionary-created shapes.
Prose markers remain atomic; marker-like path contents remain literal. Forward
stream conversion cannot discard context. Protected-text restoration uses
`--terms translate`; old line decisions remain available for unprotected text.
Changed/reordered literals produce operator review findings, while unchanged
literal vocabulary is excluded from prose diagnostics. Protection is preservation,
not secret redaction or URL validation. Vandal routes the product rule only.
`scripts/literals_smoke.py` executes file-backed neutral/restored applications
through CLI and actual operator/source-agent stdio; installed qualification imports
the scanner from the wheel and repeats these application workflows.

## Selected YAML translation

Bamboozle's `yaml_source.py` implements the original selected-string/key-protection
requirement in `.legacy/bamboozle_v0.0.1.md:303-317`. Vandal routes its shared
document adapter and supplies no YAML parser or replacement translation engine.
Rules are `{"format":"yaml","pointers":[...]}`; CLI uses repeated
`--yaml-pointer`, operator MCP uses `yaml_pointers`. Ledger, restoration, view,
source-agent and application-check paths consume that same adapter.

The supported syntax/type contract and executable commands are in README's
"Translate selected YAML values" section. One YAML 1.2 core-schema document,
unique string keys, explicit string values and bounded alias graphs are required.
Select every logical path to a shared anchored scalar. Refuse implicit changes
through omitted aliases, key ownership, merges, cycles and ambiguous keys. Keep
unselected content and syntax intact; never execute custom tag constructors.
The operator owns schema compatibility with the consuming application.

Use physical scalar spans and private original/encoded spelling context. Validate
semantic values and topology after rendering; quote type-changing replacements.
Exact unchanged restoration retains BOM, line endings and escaped/folded spelling.
Returned protected changes receive operator diagnostics; malformed selections
cannot publish. Existing historical text/Markdown/JSON/Python contexts retain
their behavior and current schema versions.

Qualification runs the full YAML-configured application through CLI, actual
operator and source-agent stdio, generated configurations, named workload checks,
drift refusal and partial-import recovery. Repeat this from an isolated installed
wheel. Preserve failing runs and distinguish this gate from global acceptance.

## Controlled lexicon contract — product 0.4.6

The product owns explicit concept/form authoring, bounded operator queries,
collision diagnostics, deterministic compilation and provenance verification.
The runtime artifact is the existing versioned `Dictionary`; the workflow does
not contain another translator or compiler. Original/profile mappings remain
unchanged. Complete schemas and executable authoring commands are in the README.

Export actual vocabulary to a new `.lexicon.json`; edit explicitly or review a
`.lexicon-patch.json` containing concept upserts/removals. Compile to a new
`.dictionary.json`, selecting a base dictionary for exact added/changed/removed
review. Inspect all bounded pages under the same review digest. A new version
never changes old ledgers or project snapshots. Source/neutral collisions, invalid
forms, duplicate concepts and ambiguous reverse partners fail before publication.
No stemming, synonym inference or silent terminology migration is permitted.

CLI applies a reviewed digest; operator MCP retains the exact preview as a
short-lived apply capability. Both use the shared reviewed-file publisher with
fresh input hashes and no-clobber output/receipt writes. Retain partial publication;
explicit metadata recovery requires the same inputs, output bytes and digest.
Compilation receipts contain exact authoring/base snapshots and lineage; verification
recompiles them and reports current input drift separately from saved consistency.
This is reproducibility evidence, not an authenticated author signature.

Authoring, patches, compiled dictionaries and receipts are private metadata under
the shared agent/view guard. None of the six lexicon tools is registered on
`serve-agent`. Operators deliberately select a published mapping for later agent
contexts or project exports. Lexicon handling does not alter encryption formats,
keys, or the independent Vandal workflow version.

## Generated fixture contract — product 0.4.7

Version-3 project policies add generated JSON record fixtures with explicit private
`.fixture-rules.json` inputs. Preserve structure and selected technical strings;
replace declared prose with templates that keep named placeholder multiplicity,
conversions/specifications, escaped braces and exact line endings. Refuse unclassified
strings, invalid IDs/references, declared invariant failures, unresolved literal-prose
dependencies, unbound consumer functions and incorrect expected case outcomes.
The rule language supports types/bounds/enums, variable sets, role counts and
conditional requirements. It is explicit and bounded, not a general JSON Schema
engine or an inference of original scene semantics.

The product verifies declared Python function locations without executing source;
the generated contract refers to their actual visible/encoded names. Consumer
behavior descriptions are operator declarations. Consumers must be selected source
files in the view. Source, shared rule and consumer inputs must remain consistent
through export. Retain production bytes and rule snapshots privately; later rule
changes cannot reinterpret an existing view.

`fixture-preview` and its operator MCP counterpart prepare without publication.
`view-export` creates neutral records plus TEMPLATE_CONTRACT.md, a public JSON
validation contract and a corpus of verified baseline/boundary/error cases.
`fixture-validate` checks candidate records with that public contract, without
requiring a reverse dictionary or operator MCP in the agent view.

Generated controls are immutable and excluded from imports. Edited generated
fixtures must still satisfy their retained contracts; production records must
still match their original snapshot before returned code is applied. Fixture edits
never overwrite production data. Existing reviewed source import and explicit
recovery retain their behavior. Version-1/2 views and supplied fixtures remain
supported. Qualification executes actual neutral and decoded dependent programs
against the generated corpus and untouched original records.

## Returned document validation — product 0.4.8

The product's bounded operator validator uses the decoder's exact document regions
and originating dictionary/format/context snapshots. `validate-return` verifies its
retained original and ledger before inspecting; project review uses private saved
view bytes for existing files and explicitly marks new files as having no baseline.
Freshness checks refuse inputs changed during inspection. Pages have stable input
digests; display truncation and incomplete analysis are reported independently.

`accepted` means no detected issue, `needs_review` means advisory findings or an
analysis limit, and `refused` means invalid document/marker/reverse translation.
Findings locate changed-line ambiguous targets, newly present source terms, finite
ASCII word/underscore suffix candidates, removed markers, decreased controlled
vocabulary and protected JSON/Markdown changes. Exact line multiplicity scopes
baseline recognition. JSON locations distinguish decoded string offsets from raw
document token positions. There is no semantic, synonym or morphology inference.

CLI `validate-return` and read-only operator MCP `validate_returned_file` share the
same service. Restoration previews and `review_project_view` include its findings;
view pagination selects an explicit translated path under the same reviewed digest.
No validator tool is registered on the source-agent surface. Vandal routes these
operations; it does not duplicate validation code or display operator evidence in
the neutral context.

Advisories do not decide intent or block deliberate reviewed changes. Manual
restoration choices, strict decoder failures, stale-input guards and reviewed
application remain authoritative. Diagnostics do not change saved import journals,
receipt formats or recovery reconstruction. An accepted diagnostic report grants
no apply authority. Bounds, finding meanings and executable commands are in README.

Qualification includes actual CLI and stdio MCP accepted/needs-review/refused
outcomes, paged mixed-format views, returned dependent programs and explicit
partial-import recovery. Separate operator encryption/decryption encloses edited
neutral bytes before they are validated and restored with their semantic context.

## Encryption/decryption routing

Readable semantic encoding is not confidentiality. Operators select authenticated
encryption explicitly. A composed pipeline is semantic encode → encrypt → decrypt
→ semantic decode using the originating dictionary and document context. Decryption
does not authorize writes or reinterpret a recipient label as independently proved
identity. Keys remain local; preserve existing key lifecycle and no-clobber behavior.

Tessera packets and BAMb5 `b5_` tokens use different formats and keys. Do not silently
try alternate decoders, rotate keys, or feed opaque ciphertext to semantic adapters.
Keep workflow metadata and encryption secrets out of the neutral agent tree.

## Supplied cipher methods — product 0.6.0

The supplied C codex is pinned at commit
`c53f0bfded51667437fc837cd33ede37745ae9c5`; its parent `834b489` precedes those
additions. Preserve original C, headers, README, license and build-file bytes.
The review and native comparison reports record provenance and observed defects.
An original design is a method reference; qualification must establish its actual
behavior before adopting it into the source-to-return pipeline.

The product's immutable classical registry derives 11 algorithm families from
that codex. Its callable byte pipeline validates exact parameter schemas and
pinned variants, bounds input/intermediate expansion, and reverses step order for
decoding. Normalize affine/shift parameters before arithmetic; require invertible
affine multipliers. Ragged Scytale and Columnar retain length without stripping
literal padding characters. Framed Polybius retains case, I/J and every byte with
canonical coordinate/literal tokens. These corrected formats explicitly differ
from lossy C formats. The README defines parameter and wire contracts; no format
inference, fallback decoder or compiler dependency is introduced into the package.

Operator `cipher-transform` and MCP `preview_cipher_transform` prepare the complete
pipeline through `ReviewedFilePlan`. Prove the inverse, pin input/recipe/result/
receipt bytes, review the digest, then publish with fresh checks and no clobber.
Recipe and receipt suffixes are private under the shared agent/view boundary.
After interrupted publication, retain output and recover only metadata using the
original digest and unchanged inputs/bytes. `cipher-verify` and
`verify_cipher_transform` reconstruct the inverse from the saved recipe and report
current input drift separately. Consistency evidence is not authentication.

Product `cipher-catalog` / `cipher_catalog` describes semantic, classical and
authenticated operations and exact classical parameter schemas. Vandal contexts
delegate to this product-owned catalog; the workflow contains no algorithm or
duplicate registry. Source-agent MCP exposes only its five source-development tools.
Classical bytes are opaque operator artifacts, not readable neutral vocabulary.
For confidentiality, explicitly compose with Tessera; reverse/decrypt transport
before semantic validation and source restoration. Existing dictionaries, saved
formats/contexts, profanity handling, BAMb5 and authenticated encryption remain
independently operable.

Native comparison uses `scripts/cipher_reference.py` with an explicit compiler
and new work directory. Windows compiler children inherit a process-local runtime
PATH and disabled error dialogs; one-source preflight and bounded process-tree
termination prevent repeating the observed missing-DLL launch failure. The report
separates 10 compatible method vectors, all 25 Polybius coordinates and three
reproduced original data-loss cases. It does not qualify all C memory behavior.

Whole-application qualification composes the cipher pipeline and authenticated
transport into the Python dependency-planner/scheduler workflow through CLI and
stdio MCP, then executes the transported neutral and restored programs. Retain
failed checks and checkpoint hashes alongside source and installed-wheel evidence.
The user-prioritized method gate originally shelved `VIG-CIPHER-YAML-01`.
Its resumed implementation and qualification are tracked in current status.

## Python source development — product 0.5.0

The explicit Python document adapter implements the original syntax-protection
requirement and extracts operational requirements from `.legacy/designlog`: format
exclusions (line 242), nonrecursive exact mappings (546–560), private literal
restoration (561–592), source-span preservation (750), and source-agent read/write
separation (2855–2898). These strengthen the existing engines; alternate registry
proposals do not replace the cipher or make workflow machinery its purpose.

`document: {"format":"python","identifiers":[...],"modules":[...],"strings":[...]}`
selects exact project-owned identifier spellings, exact project import paths and
separately selected complete literal values. Optional lists default empty. The
shared adapter uses original token spans, compiler/symbol-table validation,
collision/reflection guards, and per-occurrence private selected-string spelling
context. It preserves unselected syntax and composes with current marker/ledger
versions. Python identifier selection applies across scopes; it is not type
inference. External module/member paths stay literal. Reject ambiguous ownership,
unsupported dynamic references and mapping collisions instead of guessing.

CLI, operator MCP, saved ledgers, restoration, return validation and scoped views
share this adapter. Public view contracts disclose neutral selections only.
Generated template consumers use the actual selected Python symbol map. Project
export and import check declared import providers and bound names in complete
selected snapshots. This establishes static relationships, not application behavior;
execute complete neutral and restored dependent programs with meaningful input,
error, boundary and workload coverage. The scheduler qualification in
`scripts/python_smoke.py` retains that full workflow through CLI and stdio MCP.

Unconfigured direct source-agent tools retain their existing text semantics and source-development
authority. Vandal routes syntax-aware Python development through an explicit
source-agent policy or a prepared view. No encoding or operator connection is
introduced into the already encoded view context.
Limits, exact selection semantics and actionable refusals are documented in README.
Keep existing text/Markdown/JSON workflows and historical contexts operational.

## Native structural profile contract

The operator's `.vigiles/structure.json` selects explicit version-1 roots, suffixes,
format checks, byte/count limits, required paths, companion mappings and exclusions.
The tracked profile in this checkout protects core encoder/cipher/translator,
dictionary and agent entry points and checks Python/JSON product sources and scripts.
Other workspaces opt in independently. Product `structure-check` and operator MCP
`check_repository_structure` share bounded, read-only, paged evaluation; `audit`
combines it with existing baseline drift. The README defines the complete schema,
fixed exclusions, limits and user commands. No source language or rule is inferred.

Source-agent reads bind the active rule hash in their translation context. Writes
decode normally, validate the prospective original-domain tree, retain ordinary
source history and refuse structural violations with private receipt evidence.
Direct agent tools never expose operator findings or controls. After reconnecting,
configured structural writes require the read's context hash even in text mode.
Operator file conversions, reviewed file publication and CLI binary packet output
also enforce relevant destination rules without changing their cipher methods.

View review overlays every addition/edit/deletion before validation, so dependent
module/test additions can pass together. Successful review digests bind the profile
and complete prospective scoped tree. Unselected in-scope drift invalidates them.
Import publication rechecks the same overlay throughout the existing journaled
operation. Recovery pins retained controls; changed active rules do not rewrite
historical journals or prevent historical status inspection. Preserve refusal
receipts, partial source and failure evidence; no automatic rollback is introduced.
No-profile operations retain historical review/context digests. Outside-scope
operations do not fail for unrelated tree violations; their active control hash
still prevents silent rule changes during an operation.

Vandal includes the product fingerprint and routing instructions in its context.
It does not parse or enforce structural rules itself. The regular verifier checks
the repository profile, while source and isolated installed-package qualification
execute the complete profile-governed application through real CLI/MCP, deliberate
invalid proposals, multi-file dependencies, partial import and explicit recovery.
Profiles establish bounded structural constraints, not semantic correctness, test
quality, transaction isolation against arbitrary filesystem writers, or OS isolation.

## Verification, evidence and remaining scope

Product 0.4.9 implements literal-prefix escaping in the shared translation engine.
Marker protocol 2 reserves `__SCM_CASE_x_<lowercase UTF-8 hex>__` within the existing
CASE namespace; `x_` cannot collide with a legacy case mask. Only an exact reserved
prefix is a valid payload. Each conversion is single-pass: restored literal syntax
is never interpreted again. Corrupt/noncanonical returned tokens still fail.

New schema-3 ledgers and schema-2 view manifests pin `marker_version: 2` alongside
their mapping and document rules. Historical ledgers (1/2) and manifests (1) retain
protocol 1; regeneration, validation and return use that recorded protocol.
Keep legacy contracts and completed/pending import journals intact. A historical
view cannot opt into new syntax by editing its manifest; export a fresh view.

Source-agent reads/writes and Vandal contexts report their marker version. Preserve
existing escape tokens. For new raw examples, use the dictionary-free product
helper `bamboozle.markers.quote_prefixes` on the new snippet only. Do not apply it
to encoded content or intended literal/case controls. Structured adapters quote
only translated regions. Operator validation, restoration and view import share
the recorded decoder; encryption remains a separate explicit operation.

Qualify real source and installed-package CLI/MCP paths, including edited/generated
programs, protected structures, saved dictionary/format context, stale or malformed
inputs, original-byte restoration, failure recovery and crypto composition. Verify
the Vandal-generated original-agent configuration exposes only its five source-development tools;
verify operator configuration separately. Keep Vandal runtime/helper tests distinct
from product package tests. Local deterministic inputs do not prove external model
behavior, remote CI, production assets, client registration or host isolation.

Run `uv run --locked python scripts/verify.py --package` from the application root.
Archive exact previous reports before replacing current evidence. Keep active
planning in `VIGILES_STATUS.json`; retain detailed reports under the existing
`.mivim/vigiles/reports/current/` and `archive/` layout. The tracked `.vigiles/skills/`
subtree contains workflow instructions, and `.vigiles/structure.json` is the tracked
operator profile; other `.vigiles/` paths remain private runtime/fixture storage.
Update this specification when implementation changes,
without rewriting the archived vanilla contracts or claiming unfinished gates.

Broader format adapters, richer fixture rule
operators and terminology diagnostics beyond finite suffix/count evidence remain
core development opportunities.
Existing identity/host acceptance gates remain separate; completing one source
capability does not declare the entire application complete.

## Managed local recipient and delivery contract

VIG-IDENTITY-01 adds product `identity.py` and `transport.py`, integrated into the
existing file and complete-project pipeline through CLI and operator MCP. The
[managed transport contract](../docs/managed-transport.md) specifies actionable
provisioning, lifecycle, packet versions, exact replay binding, operation recovery
and trust limits. Vandal routes this interface; it owns no keys or cipher methods.

Managed schema-2 Tessera authenticates explicit sender, recipient, key fingerprint,
message ID, purpose and validity with the payload. No legacy downgrade is allowed.
Existing schema-1 raw-key operations, classical framing, semantic mappings and
retained translation contexts remain compatible. Registration explicitly binds a
fresh pair key. Rotation retires old sending keys; receiving admits unexpired
messages issued through retirement. Revocation permanently disables that key for
new delivery and pending recovery. No key material enters reports or agent views.

Read-only review never reserves delivery. Before file/extraction/source writes,
local admission pins the exact packet and operation receipt under custody locking.
A sender/recipient/message tuple cannot be admitted again by JSON reserialization,
changing its path or choosing another output. The existing domain receipt completes
admission without a second commit. Retained admission plus exact intent permits
explicit partial recovery after expiry, subject to current revocation. An unchanged
managed return is also consumed on apply. Historical status uses retained context
and remains inspectable after lifecycle changes. Operation journals may precede
admission, but payload writes never do; this crash boundary is explicitly tested.

The local trust model assumes protected operator state and a trustworthy clock.
Out-of-band peer authentication/key distribution, custody ACLs, cloned-root/global
replay policy, administrator rollback and independent identity verification remain
external acceptance concerns. No production deployment or cryptographic certification
is established by synthetic local source/installed application qualification.
