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

- `agent`: original source accessed only through `read_safe_file` and
  `write_safe_file`. These are whole-term text tools, suitable for explicitly
  selected plain text/code. Preserve case, literal and prefix-escape markers. Re-read changed
  files or present their prior source hash after reconnecting. Do not mix raw
  source reads into a neutral agent context.
- `view --view-id ID`: a previously exported neutral tree. Use ordinary file tools
  directly in the returned workspace, read `BAMBOOZLE_VIEW.md`, and preserve its
  rules. Never encode this tree a second time. It gets no original-source or
  operator MCP connection. This is the route for mixed-format multi-file work.
- `operator`: original-domain previews, mapping selection, export, reviewed
  restoration/import and recovery. It may show sensitive source terms and must
  remain outside the neutral agent's tool pool.

## Run the Bamboozle cycle

For structured work, select exact files with a version-2 project-view policy.
Use `document: {"format":"markdown"}` for Markdown prose or
`document: {"format":"json","pointers":["/prompt","/tags/0"]}` for selected
JSON strings under a `translate` rule. Use copy for intentionally visible exact
content and supplied fixtures when production content must stay local. Do not
guess a file's format or expose the entire repository by default. The
[README](../../../README.md) contains executable CLI sequences and policy schemas.

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

For syntax-aware Python work use an explicit `python` document rule in a project
view. Select exact project-owned identifiers, declare project import module paths,
and select symbolic strings separately. The agent reads neutral selections from
the generated contract. Preserve external API names and unselected literals;
resolve collisions, missing providers, reflection or ownership errors rather than
falling back to global text replacement. The README defines supported semantics.
Generated fixture consumers use the same selected symbol map. Execute complete
dependent programs and error/workload cases in both vocabularies before claiming
the implementation complete. Keep the direct source-agent text tools separate.

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
and select its Python consumers as copy/text-translate source. In the neutral view,
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
