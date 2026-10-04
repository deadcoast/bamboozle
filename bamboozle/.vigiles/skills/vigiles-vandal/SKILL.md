---
name: vigiles-vandal
description: Operate and develop Bamboozle in this MIVIM repository under vigiles-vandal_0.2, selecting the correct original-source agent, neutral-view, operator, and encryption interfaces. Applies to this repository's cipher and translation work; it is not a general agent framework.
---

# Vandal: Bamboozle repository workflow

Read the active [Vandal specification](../../../.mivim/vigiles-vandal_0.2.md)
and [status](../../../VIGILES_STATUS.json). Paths here resolve from this skill to
the Bamboozle application root. Bamboozle owns the engines and public interfaces;
Vandal owns this repository's agent operating procedure. Product and workflow
versions are independent. Keep the user's current scope ahead of inherited queues.

## Select the actual context

From the application root, `uv run --locked python scripts/vandal.py --mode MODE`
generates a read-only context with absolute paths and one MCP configuration.
Use `mcp_config` in the chosen client's configuration. Generation does not register
or launch a client, alter configuration files, or establish OS isolation.

- `agent`: discover with `list_safe_files`, search encoded content with
  `search_safe_files`, read files/ranges with `read_safe_file`, apply exact edits
  with `edit_safe_file`, and publish whole/new files with `write_safe_file`.
  Add `--policy source.agent-policy.json` to route selected
  Python/Markdown/JSON/YAML/text files through the product's existing document adapters.
  Without it, these retain whole-term text behavior. Preserve case, literal and
  prefix-escape markers. After reconnecting with a policy, submit both prior
  source and context hashes; re-read changed files. Do not mix raw
  source reads into a neutral agent context.
- `view --view-id ID`: a previously exported neutral tree. Use ordinary file tools
  directly in the returned workspace, read `BAMBOOZLE_VIEW.md`, and preserve its
  rules. Never encode this tree a second time. It gets no original-source or
  operator MCP connection. This is the route for mixed-format multi-file work.
- `operator`: original-domain previews, mapping selection, export, reviewed
  restoration/import and recovery. It may show sensitive source terms and must
  remain outside the neutral agent's tool pool.

## Run the Bamboozle cycle

For direct source work, use an operator-owned `.agent-policy.json` with version-2
file rules and launch `serve-agent --policy` or Vandal `--mode agent --policy`.
Keep policy/dictionary inputs private; the agent receives content and opaque hashes.
New-file directory rules also govern subsequent reads of generated files. Source
writes create nested package directories after translation and stale-input checks.
Test the complete dependent application; no view or encrypted packet is required.

List the permitted source paths, search the neutral vocabulary, then read the
relevant file/range. Use the returned neutral document rules and listing's generated
directory rules. For targeted edits pass all three source/context/neutral hashes
and exact old/new text anchors; repeated anchors need a 1-based occurrence. Every
anchor refers to the same pre-edit document. Never replace a whole file with a
ranged excerpt. Re-read after stale/ambiguous/overlapping edits are refused.
Page navigation with the returned snapshot digest and restart on changed observations.
Unreadable search records mean incomplete coverage. Narrow scope on bounded-scan
errors. Do not use raw filesystem searches to bypass the cipher or private policy.

For application execution, the operator adds `--checks ...agent-checks.json` with
an explicit source policy. Vandal forwards this to the product server; it does not
implement a runner. Use `list_safe_checks`, select its check ID and snapshot hash,
then call `run_safe_check`. Inspect the actual exit status, neutral diagnostics,
capture/cleanup flags and source freshness. Correct failures with the normal
source edit/write tools and obtain a new snapshot before rerunning. Do not change
private check definitions or invent shell commands through this surface.

After reconnecting, `inspect_safe_check` reads the retained run without executing
anything. A historical result only describes current code when
`matches_current_source` is true. Preserve failed/incomplete runs. New checks use
fresh copies, and check-generated files never import themselves into source.
Configure explicit neutral `python_paths` for `src/` layouts and provide required
dependencies through the server environment. Process ownership/output bounds do
not provide OS sandboxing; operator-selected project checks execute under that
account. Exercise a failing application check, source correction and passing
rerun through actual MCP and installed-package qualification.

For reviewed multi-file work, select exact files with a version-2 project-view policy.
Use `document: {"format":"markdown"}` for Markdown prose or
`document: {"format":"json","pointers":["/prompt","/tags/0"]}` for selected
JSON strings under a `translate` rule. Use copy for intentionally visible exact
content and supplied fixtures when production content must stay local. Do not
guess a file's format or expose the entire repository by default. The
[README](../../../README.md) contains executable CLI sequences and policy schemas.

For every nested JSON string value, select
`document: {"format":"json","values_only":true}` in either policy or use
operator `encode --json-values-only`. Keep exact pointers for narrower selection.
The recursive rule applies to new returned/generated values too; keys and
nonstring tokens stay literal. Review key/type/nonstring changes separately.
Keep private spelling context local, preserve markers and execute the complete
consumer after edits or recovery. Moved tokens recover spelling only when
unambiguous. See `docs/json-values.md`; Vandal supplies no second JSON engine.

For text containing paths/URLs that must remain exact, explicitly select
`document: {"format":"text","protect":["paths","urls"]}` in source-agent/view
policies, or `encode --format text --protect paths --protect urls`. Either class
may be selected alone. Read `docs/text-literals.md` for supported lexical spellings.
The product owns recognition, private span context and inverse checks. Keep literal
contents visible and exact; review changed/reordered literal findings and execute
the consuming application. Preserve prose markers atomically. Use file-to-file
encoding to retain context and `restore-edits --terms translate` for reviewed
returns. Never silently enable this rule for old saved operations.

For YAML use `document: {"format":"yaml","pointers":[...]}` in either policy,
or operator `encode --format yaml --yaml-pointer ...`. Keep all selected paths
present as strings. If an anchor is shared, select every affected alias value
path; do not introduce an unselected alias to a translated scalar. Preserve keys,
tags, comments and anchor/alias names. Merge-owned selections, nonstring keys,
cycles and document streams are refused under the documented YAML 1.2 contract.
Original escaped/folded spellings remain in private context. Review
`protected_yaml_changed` findings for deliberate configuration edits and execute
the dependent application with both the neutral and restored configuration.

Export with `bamboozle view-export`, generate the view context, complete the agent
edits and run the relevant checks in that view. Operator `view-review` displays
decoded changes. Resolve conflicts and inspect any truncated diff in full before
applying its exact `review_sha256`. Select intended deletions on both review and
apply. New files must satisfy the saved directory and format policy. Verify the
returned original-vocabulary program/configuration, not just the neutral text.

For single files, `encode --format markdown` or
`encode --format json --json-pointer /prompt` retains rules in a private ledger;
ordinary decode uses that snapshot automatically. Newly named returns use
`--ledger` explicitly. Strict decode requires unchanged encoding. Structured
`restore-edits` uses `--terms translate`; its saved field/format rules replace
line-based choices, and literal markers express neutral terms intended literally.

Marker protocol 2 supports literal reserved syntax in original source. Preserve
`__SCM_CASE_x_<hex>__` prefix escapes and never decode their result a second time.
Use `bamboozle.markers.quote_prefixes` only for a newly authored raw marker snippet;
it needs no dictionary. Do not apply it to an encoded document or intended control
markers. New ledgers (schema 3) and view manifests (schema 2) pin protocol 2.
Historical ledgers/manifests retain protocol 1: do not insert new escape tokens or
rewrite their saved contexts. Export fresh when new marker examples are required.
The generated Vandal context reports the selected view's actual marker protocol.

## Author vocabulary in operator context

For syntax-aware Python work use an explicit `python` document rule in a direct
source-agent policy or project view. Select exact project-owned identifiers, declare project import module paths,
and select symbolic strings separately. The agent reads neutral selections from
the generated contract. Preserve external API names and unselected literals;
resolve collisions, missing providers, reflection or ownership errors rather than
falling back to global text replacement. The README defines supported semantics.
Generated fixture consumers use the same selected symbol map. Execute complete
dependent programs and error/workload cases in both vocabularies before claiming
the implementation complete. Unconfigured direct tools retain whole-text semantics.

Use `lexicon-export` to start from the actual selected dictionary/profile, then
`lexicon-query`/`lexicon-check` to inspect explicit concept forms. Review
`lexicon-edit --patch` into a separate authoring file or edit that file directly.
`lexicon-compile --base` displays exact added/changed/removed mappings and returns
a review digest. Inspect all change pages under that same digest before applying
with `--apply --review-sha256`. On operator MCP, use the matching lexicon preview
tools followed by `apply_preview`; never attach them to a neutral agent.

Every spelling/grammatical form requires a unique neutral partner. Keep existing
profiles intact and publish new versions. Select the compiled `.dictionary.json`
explicitly when exporting a view or opening an agent server; old ledgers/views
retain their original mapping. `lexicon-verify` checks retained source/base
snapshots and reports later input drift separately. Keep required private suffixes
and receipts outside agent content. After partial publication, retain inputs/output
and use explicit `--recover-metadata` with the original reviewed digest.

## Prepare generated template fixtures

Use `fixture-preview SOURCE --rules CONFIG.fixture-rules.json` in operator context.
Resolve unclassified strings, placeholder/count/reference violations, literal-prose
dependencies and unbound consumer symbols before export. Choose explicit neutral
templates and expected boundary/error cases from the actual processing contract.
The product validates rules and source bindings; Vandal does not implement a second
fixture generator or infer domain semantics.

Use a version-3 view policy with `{"policy":"fixture","rules":"...fixture-rules.json"}`
and select its Python consumers as copy/text/python-translate source. In the neutral view,
read TEMPLATE_CONTRACT.md and use the public TEMPLATE_CONTRACT.json validator and
TEMPLATE_CASES.json corpus. Execute the application against cases; a passing fixture
validator alone does not prove application behavior. Keep generated controls unchanged
and add new test files under the policy's allowed directories.

Review decoded source returns with the normal view tools. Invalid/absent generated
fixtures or changed production records block application; fixture text never becomes
production data. Retain partial imports and recover explicitly with the original
digest. Run the restored program against production-shaped originals as well as
the neutral cases. Operator MCP fixture tools remain outside the agent connection.

## Inspect returned vocabulary and structure

The operator runs `structure-check` or `check_repository_structure` against the
active `.vigiles/structure.json`; a draft `--rules ...structure-profile.json` is
read-only. This checkout tracks a profile protecting its core product files.
Bamboozle owns profile parsing, audits and source publication guards. Vandal
reports the active fingerprint and routes tools; it supplies no second validator.

Resolve required-path, suffix, syntax, byte/count and companion findings before
publication. Page findings under the same check hash; truncated/incomplete results
require narrower scope or correction. Agent write contexts include the active
profile even in whole-text mode. Re-read after rule changes and keep controls
private. Use a reviewed multi-file view to add a module and its companion together.
Inspect `structural_validation` on the decoded proposed tree. A review pins the
rules and whole scoped result, including unselected files in those roots.

Refusals retain operator evidence under `.vigiles/structure/refusals/`. For partial
imports, preserve snapshots/journal, inspect historical `view-status`, and recover
only with the original profile and unchanged scoped inputs. Restore deliberate
operator drift explicitly; never weaken rules or replace retained intent merely
to obtain a pass. Execute the dependent neutral and restored applications because
syntax, required paths and companion existence cannot prove their behavior.

Run operator `validate-return EDITED --original ORIGINAL --ledger LEDGER`, or
`validate_returned_file`, using the originating retained context. `restore-edits`
and `view-review` also include `return_validation`. Findings may expose original
terms; keep this tool and its reports outside the neutral agent's connection.

`accepted` means no issue detected, not proven intent or correct code. Resolve
`refused` input/marker/decoder errors before restoration. For `needs_review`, inspect
located ambiguous targets, undeclared suffix candidates, source terms, missing
markers, term-count decreases and protected-content changes. Never invent a reverse
mapping from a heuristic. Choose an existing explicit form, mark a literal target,
or deliberately retain the edit using the existing reviewed application path.
New vocabulary versions do not change the mapping of an already exported view.

Use `offset`/`max_findings` (`--offset`/`--max-findings`) for single-file pages.
For a view, select `validation_path`, `validation_offset`, `max_findings` on
`review_project_view`, or `view-review --validation-path PATH --offset N` on CLI.
Compare the same validation/review digests across pages and inspect the full diff
if analysis is incomplete. The validator grants no separate apply capability;
manual term choices and existing review/apply/recovery remain authoritative.

If transport is encrypted, decrypt explicitly before semantic validation. Test the
restored program as well as the neutral result. A validator result cannot establish
application semantics, external-model acceptance or correct key governance.

## Keep encryption separate and composable

For explicit peer custody use the product's [managed transport guide](../../../docs/managed-transport.md).
Initialize each local identity once and register an operator-provisioned pair key
outside neutral workspaces. Select `--peer` (MCP `peer`) on file/project transport;
it names the destination when sending and expected sender when receiving. Do not
mix it with raw `key`/`recipient`. Inspect `identity-status` before digest-bound
rotation/revocation; retired keys receive only in-flight messages and revoked keys
block pending writes. Never silently reissue, reuse or downgrade a key.

Preview does not consume a managed return; exact reviewed apply does, including
no-edit returns. Inspect `delivery-status` after interrupted publication. File and
extraction recovery repeat the exact packet/destination with `--recover`. Source
recovery omits packet/peer/credentials and uses its retained review certificate;
current revocation remains authoritative. Preserve admissions and domain receipts.
A completed historical receipt remains readable after key revocation. These local
controls do not supply independent identity verification or global replay policy.


For complete project exchange, export a fresh scoped view and use operator
`view-pack` / `pack_project_view` with a `.key`, recipient and optional private
classical recipe. At the receiver, `exchange-open` / `open_project_exchange`
authenticates and reverses transport into a new readable workspace. Use product
`exchange-context`, or repository `scripts/vandal.py --mode exchange --exchange-id
ID`, to select that workspace with ordinary file tools and no encoder/operator
connection. Keys, recipes and private journals stay outside the agent directory.

After editing, generating dependent modules and executing the complete neutral
application, use `exchange-return` / `pack_project_return`. At the source owner,
pass `return_packet`, `key`, `recipient` and optional `recipe` into the existing
view review/apply operations (CLI options use hyphens). Inspect decoded diffs and
all paged diagnostics; keep explicit deletions and the original new-file allowlist.
Apply the exact reviewed packet, then execute the restored application. The local
neutral view is preserved. Never copy private inverse dictionaries into a receiver.

Extraction recovery repeats exact `exchange-open` inputs with `--recover`.
Source import recovery uses retained authenticated snapshots: inspect `view-status`
and call `view-apply --recover` with the retained review digest, omitting packet,
key, recipient, recipe and deletion arguments. Preserve partial state and failed
evidence. New development cycles need fresh views. Local completed-import rejection
does not close recipient identity, key lifecycle or network replay gates.

Use product `cipher-catalog` or operator MCP `cipher_catalog` to discover exact
methods and parameter schemas. Vandal contexts include that same product-owned
catalog. The supplied C originals are pinned at `c53f0bf`; keep their original
files and provenance intact. Product `classical.Pipeline` provides the qualified
byte methods; its ragged transpositions and framed Polybius differ deliberately
from the original lossy formats. Never infer a variant from ciphertext.

Classical recipes belong in private `.cipher-recipe.json` files. Operator
`cipher-transform` previews a complete forward/reverse pipeline; apply with its
exact digest, or use `preview_cipher_transform` / `apply_preview`. `cipher-verify`
and `verify_cipher_transform` check saved receipts and inverse bytes, reporting
input drift separately. Retain partial outputs and use explicit metadata recovery.
Do not attach these operator tools to the neutral source-agent connection or feed
opaque transformed bytes to an agent expected to edit readable source. Reverse
transport before semantic validation and execute the returned application.

For native method comparisons, use `scripts/cipher_reference.py` with an explicit
compiler and new scratch directory. Its Windows launcher supplies the compiler's
runtime directory locally, suppresses inherited error dialogs, preflights one
source, and bounds execution. Do not bypass it with a batch of direct `gcc.exe`
invocations: a missing child DLL previously caused cascading desktop dialogs.
The C originals are method references, not memory-sanitizer-qualified runtime code.

Semantic encode/decode makes readable neutral vocabulary; it does not encrypt.
For authenticated file confidentiality, the operator uses Bamboozle
`keygen`, `seal` and `open` with explicit key, recipient and distinct output paths.
Decrypt an encrypted returned payload before semantic decoding with its original
ledger or view context. Keep keys out of agent content, logs and reports.

BAMb5's separate `b5_` AES-SIV token cipher remains at
`python -m workshop.scrape.bamb5`: `enc`/`dec` operate terms, `enc-json`/`dec-json`
operate explicitly selected JSON keys. Its key is selected through the workshop
configuration; do not rotate it or reinterpret its tokens as semantic vocabulary.
Use these operations only when the task requires token encryption. Workshop
promotion/audit tools support the product and are not prerequisites for ordinary
translation or agent development.

## Finish and recover truthfully

On partial view import, retain the view, snapshots and journal. Inspect
`view-status`; recover explicitly with the original digest after resolving
external conflicts. Do not regenerate intent, roll back, or delete failed evidence.
Completed receipts describe history; assess current source/view drift separately.

Develop a complete callable path from input to returned source, then run focused
tests and the repository verifier with `--package`. Retain failed runs, update the
active specification/status and current evidence through the existing archive
writer. Follow the evidence layout in `AGENTS.md`. Do not treat local synthetic
tests as external model acceptance or advance to deployment while independent
core work remains.
