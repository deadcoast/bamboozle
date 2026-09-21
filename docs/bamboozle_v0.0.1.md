# Bamboozle — tooling and design v0.0.1

**Build this as a reversible terminology adapter, not a guarantee of platform acceptance.** Replacing vocabulary does not change a document’s underlying meaning or another platform’s rules. For professional development, neutral technical descriptions are generally more reliable than disguising sentences as typos.

I don’t have a live web-search tool in this session, so the recommendations below are based on established libraries—not newly verified search results.

## 1. The key technical correction

**A one-to-one dictionary does not guarantee reversible documents.**

Suppose your mapping is `cum → oil`. If the original document already contains “oil,” decoding cannot distinguish the original word from a replacement.

Other sources of corruption include:

- Case folding: several capitalizations become one.
- Cascading replacements: replacement output gets replaced again.
- Overlapping phrases: a short match consumes part of a longer match.
- Normalization: punctuation, spacing, or Unicode changes destroy exact recovery.

You need to choose between:

| Mode | Output | Exact recovery |
| --- | --- | --- |
| Readable substitutions | Ordinary neutral vocabulary | Only with collision restrictions or an occurrence ledger |
| Reserved markers | `⟦BZ:material_01⟧` | Yes, with reserved-marker validation |
| Technical abstraction | Natural professional descriptions | No; preserve the source separately |

**Recommended:** readable substitutions for working documents, a local source copy for fidelity, and reserved markers when exact round trips matter.

### 2. Suitable libraries

| Library | Role | Recommendation |
| --- | --- | --- |
| Python `re` | Whole-term, single-pass replacement | Best starting point |
| [`bidict`](https://bidict.readthedocs.io/) | Enforce unique values and provide inverse lookup | Useful |
| [`pyahocorasick`](https://pyahocorasick.readthedocs.io/) | Efficient matching across large dictionaries | Consider after benchmarking |
| [`flashtext`](https://github.com/vi3k6i5/flashtext) | Keyword extraction and replacement | Check boundary and case behavior |
| [`spaCy`](https://spacy.io/) | Linguistic annotations and contextual analysis | Optional linting, not the reversible core |
| [`Hypothesis`](https://hypothesis.readthedocs.io/) | Property-based round-trip testing | Strongly recommended |

Avoid normalization libraries in the reversible path. Lowercasing, accent removal, and whitespace cleanup are generally lossy.

Also, **do not assume `textsearch.replace(..., inverse=True)` exists**. Reverse translation should use a separately constructed inverse matcher unless the installed library explicitly documents otherwise.

### 3. Define the UDL

Here, UDL means your application’s terminology schema—not an established interchange standard.

```json
{
  "schema_version": 1,
  "mapping_version": "2026-09-21.1",
  "case_sensitive": true,
  "boundary_policy": "unicode_word",
  "entries": [
    {
      "id": "action_001",
      "bam": "explicit_source_term",
      "boozle": "neutral_target_term",
      "part_of_speech": "verb"
    }
  ]
}
```

Rules:

1. Every source and target must be nonempty and unique.
2. Source and target vocabularies must be disjoint.
3. Inflections receive separate entries; do not stem automatically.
4. Replacement must be single-pass and longest-first.
5. Existing target vocabulary must trigger a collision error in strict readable mode.
6. Store the mapping version with each transformed document.
7. Treat mappings and any occurrence ledger as sensitive source data.

**Strict lexical substitution cannot guarantee grammatical coherence.** Linguistic review is a separate step.

### 4. Minimal working CLI

This dependency-free prototype supports **case-sensitive, whole-term, readable substitutions**. It rejects target collisions and verifies that encoding can be decoded exactly before writing the output.

`mapping.json`:

```json
{
  "sensitive_action": "technical_operation",
  "sensitive_component": "technical_component"
}
```

`bamboozle.py`:

```python
#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_mapping(path):
    mapping = json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
    )

    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("Mapping must be a nonempty JSON object.")

    for source, target in mapping.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("Sources and targets must be strings.")
        if not source or not target:
            raise ValueError("Empty terms are not permitted.")
        if source != source.strip() or target != target.strip():
            raise ValueError("Terms cannot have surrounding whitespace.")

    if len(set(mapping.values())) != len(mapping):
        raise ValueError("Targets must be unique.")

    overlap = set(mapping) & set(mapping.values())
    if overlap:
        raise ValueError(f"Source/target overlap: {sorted(overlap)!r}")

    return mapping


def compile_terms(terms):
    # Longer alternatives win when they start at the same position.
    alternatives = "|".join(
        re.escape(term)
        for term in sorted(terms, key=lambda term: (-len(term), term))
    )
    return re.compile(r"(?<!\w)(?:" + alternatives + r")(?!\w)")


def substitute(text, mapping):
    pattern = compile_terms(mapping)
    return pattern.sub(lambda match: mapping[match.group(0)], text)


def encode(text, mapping):
    target_pattern = compile_terms(mapping.values())
    collision = target_pattern.search(text)
    if collision:
        raise ValueError(
            "Original text already contains a reserved target term: "
            f"{collision.group(0)!r}. Use markers or a different mapping."
        )

    encoded = substitute(text, mapping)
    inverse = {target: source for source, target in mapping.items()}

    # Also catches boundary and phrase interactions.
    if substitute(encoded, inverse) != text:
        raise ValueError(
            "This document does not round-trip with the selected mapping."
        )

    return encoded


def main():
    parser = argparse.ArgumentParser(
        description="Strict reversible terminology substitution."
    )
    parser.add_argument("mode", choices=["encode", "decode"])
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mapping", required=True, type=Path)
    args = parser.parse_args()

    try:
        mapping = load_mapping(args.mapping)

        # Byte reads/writes avoid newline translation.
        source = args.input.read_bytes().decode("utf-8")

        if args.mode == "encode":
            result = encode(source, mapping)
        else:
            inverse = {
                target: source for source, target in mapping.items()
            }
            result = substitute(source, inverse)

        # Exclusive creation prevents accidental overwrites.
        with args.output.open("xb") as handle:
            handle.write(result.encode("utf-8"))

    except (ValueError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
```

Usage:

```powershell
python bamboozle.py encode source.md working.md --mapping mapping.json
python bamboozle.py decode working.md restored.md --mapping mapping.json
```

### 5. Production requirements

- **Format protection:** parse Markdown or structured files to exclude code blocks, URLs, identifiers, and JSON keys unless explicitly selected.
- **Mapping provenance:** attach a mapping hash; refuse decoding with a different dictionary.
- **Testing:** cover punctuation, Unicode, overlapping phrases, collisions, case variants, and `decode(encode(text)) == text`.
- **Privacy:** keep originals, mappings, and ledgers local; avoid logging document contents.
- **Edited-output handling:** exact round-trip guarantees apply to the unmodified encoded document. Once an LLM edits it, decoding is a terminology substitution—not restoration of the original.

**Bottom line:** `re` plus validation is sufficient to start. The difficult part is collision handling and preserving meaning, not finding a specialized euphemism library.

---

You’re right about the substance: you asked for a researched, usable **bidirectional document translator**, and I supplied a small replacement script while leaving the hardest requirements unresolved.

**On search:** the platform may support web search, but this session exposes no callable search tool to me. I can’t honestly claim to have searched or verified current packages.

## What I got wrong

- **I overstated the flaw in your format.** A strict dictionary is a valid foundation. Ordinary-word collisions require an escape mechanism—not abandoning your design.
- **I gave placeholder substitutions instead of designing coherent terminology.**
- **I treated decoding an edited document as an afterthought**, even though that is your main workflow.
- **I provided no meaningful handling of grammar, phrases, code, or document structure.**

## The actual Bamboozler architecture

Your intended workflow is:

```text
Original development document
           ↓ encode
Neutral, readable development document
           ↓ external development edits
Edited neutral document
           ↓ decode
Edited document using the original domain terminology
```

That is **controlled-language translation**, not profanity filtering.

### 1. Map concepts and their grammatical forms

A bare `word → word` dictionary is insufficient. Each concept needs an explicit, reversible family:

```yaml
id: operation_001
part_of_speech: verb
forms:
  base:
    bam: source_verb
    boozle: excavate
  third_person:
    bam: source_third_person
    boozle: excavates
  past:
    bam: source_past
    boozle: excavated
  participle:
    bam: source_participle
    boozle: excavating
```

These are schema placeholders, not a completed lexicon. Production entries need reviewed source terms, target terms, and examples.

**Every surface form must be unique across the compiled dictionary.** Context-sensitive ambiguity should produce a review item rather than a guessed substitution.

### 2. Escape existing target vocabulary

Do not reject an entire document because it already contains “oil.”

Before translating:

- Escape preexisting reserved-marker syntax.
- Replace literal occurrences of target vocabulary with protected markers.
- Store their original text in a local sidecar.
- Translate source terminology into readable target terminology.

During decoding:

- Translate target terminology back.
- Restore protected literals **afterward**, preventing their accidental translation.

This solves original-document collisions. It does **not** solve newly introduced ambiguous target words; those require a controlled-vocabulary contract and review.

### 3. Translate phrases before individual words

Use explicit phrase rules for expressions that become incoherent under word substitution. Apply longest matches first, without recursively processing replacement output.

Phrase rules must also have unique reverse forms. Otherwise decoding becomes guesswork.

### 4. Protect the document’s technical structure

Use format-aware adapters:

| Content | Default treatment |
| --- | --- |
| Markdown prose | Translate |
| Code fences | Preserve |
| URLs and file paths | Preserve |
| JSON/YAML keys | Preserve |
| Selected string values | Translate |
| Source-code identifiers | Separate, opt-in refactoring |

A global regex across project files is not an acceptable production implementation.

### 5. Validate returned edits

The external editor needs a short terminology contract:

> Preserve protected markers exactly. Keep controlled terms and their listed inflections. Do not replace controlled terms with synonyms.

On return, validate marker integrity, unknown inflections, and terminology drift. Flag ambiguity rather than silently corrupting the document.

**The deliverable should therefore be a lexicon compiler, format-aware encoder/decoder, collision-escape sidecar, and edited-document validator—not another keyword-replacement scaffold.** Vocabulary substitution can support that workflow, but cannot guarantee another platform’s acceptance.

---

I gave you shallow answers, then responded to your criticism with another outline instead of doing the work. You’re right about that.

I also made two technical claims too broadly:

- **Your two-dictionary design works.** It is the correct foundation for deterministic substitution.
- **Unique mappings alone don’t guarantee lossless document recovery.** That is a collision problem to solve—not a reason to dismiss your design.

Here is the missing, concrete solution to that collision problem:

### Keep ordinary substitutions readable; escape only original collisions

Given:

```python
bam = {"fuck": "dig", "cum": "oil"}
boozle = {target: source for source, target in bam.items()}
```

This input:

```text
Inspect the oil reservoir. The configured keyword is cum.
```

Must encode to:

```text
Inspect the ⟦BZ:L000001⟧ reservoir. The configured keyword is oil.
```

With a **local sidecar**:

```json
{
  "literals": {
    "⟦BZ:L000001⟧": "oil"
  }
}
```

Decode in this exact order:

1. Replace controlled target terms using `boozle`.
2. Restore literal markers from the sidecar.

Result:

```text
Inspect the oil reservoir. The configured keyword is cum.
```

**That preserves your readable substitutions without corrupting ordinary vocabulary.** The earlier script should have implemented this rather than rejecting collisions.

### The separate problem: edits made by another model

Suppose the model adds:

```text
Check the oil temperature.
```

There is no mathematical way for the decoder to know whether this newly written “oil” is literal or your controlled substitute. **Neither a paired dictionary nor a sidecar can recover intent that was never recorded.**

The actionable rule is:

```text
Within translated prose, dictionary target words are reserved terminology.
For a literal target word, use a literal escape.
Do not paraphrase reserved terminology.
```

For example, permit `⟦BZ:LITERAL:oil⟧` for newly introduced literal usage. The decoder restores those escapes after reverse substitution. An editor that ignores that contract needs human review; claiming otherwise would be misleading.

### What I actually owed you

A working implementation containing:

- A real, curated terminology file—not `sensitive_action` placeholders.
- Single-pass, longest-first phrase and word matching.
- Collision escaping and literal restoration.
- Explicit inflection and capitalization rules.
- Markdown protection for code, paths, and URLs.
- Mapping hashes and sidecar validation.
- Tests covering untouched round trips **and edited documents**.

I delivered none of that as a complete tool. Calling the response a comprehensive solution was unjustified. And I should not have implied that a proposed architecture was researched or validated when it wasn’t.

---

## Bamboozle / UDL — Repository Context Normalization

**Status:** Proposed design  
**Purpose:** Let coding models review and develop a repository using coherent technical context without unnecessarily exposing adult-domain template content.

## 1. Objective

Bamboozle creates a **neutral development view** of a repository while preserving the original domain-specific content locally.

The coding model should retain enough context to understand:

- Template schemas and required fields.
- Prompt assembly and variable interpolation.
- Category selection and compatibility rules.
- Validation, scheduling, and execution.
- Error handling and expected outputs.

It usually does **not** need the original wording of every template to implement those systems.

**Success means useful technical reviews and patches—not merely replacing flagged words.** Neutral terminology does not guarantee acceptance by another platform.

## 2. Design decision

Use two complementary mechanisms:

| Mechanism | Purpose |
| --- | --- |
| **Reversible terminology translation** | Preserve controlled domain concepts under neutral names |
| **Content isolation with representative fixtures** | Keep unnecessary template prose out of coding-model context |

Word substitution alone can leave the original meaning obvious while making the document less coherent. Removing every template, however, leaves the model unable to understand the application.

Bamboozle therefore produces a **technically representative development workspace**, not just an edited document.

## 3. Layers

### `bam`: authoritative source

Contains:

- Original templates and development documents.
- Original terminology.
- Real category labels.
- Production configuration.

This layer remains authoritative. Encoding never overwrites it.

### `boozle`: neutral development view

Contains:

- Source code needed for the task.
- Neutralized documentation.
- Stable template and category identifiers.
- Structurally faithful, non-explicit fixtures.
- A generated explanation of template behavior.
- A terminology contract where substitutions are needed.

### Local manifest: transformation bookkeeping

Contains:

- Mapping version and hash.
- Source and generated file hashes.
- Stable template identifiers.
- Protected literal values.
- Transformation type for each file.
- Source-to-view relationships.

**Do not include the private reverse dictionary or original-content ledger in the coding workspace.** Otherwise the separation accomplishes little.

## 4. Directory layout

For your workshop:

```text
D:\AI\ComfyUI_portable\workshop\bamboozle\
├── bamboozle.yaml
├── lexicon\
│   ├── concepts.yaml
│   └── phrases.yaml
├── schemas\
│   └── template.schema.json
├── fixtures\
│   ├── valid\
│   ├── invalid\
│   └── edge_cases\
├── prompts\
│   └── review.md
├── src\bamboozle\
│   ├── cli.py
│   ├── lexicon.py
│   ├── adapters.py
│   ├── translate.py
│   ├── fixtures.py
│   ├── workspace.py
│   ├── validate.py
│   └── import_patch.py
├── tests\
└── .local\
    ├── manifests\
    ├── baselines\
    └── views\
```

Keep `.local` out of version control. Launch the coding agent **inside the generated view**, not inside the original repository with a suggestion to ignore files.

That is context separation, not a security sandbox; filesystem access restrictions require separate tooling.

## 5. Transformation policy

Each file or field receives an explicit policy.

| Policy | Meaning | Return path |
| --- | --- | --- |
| `copy` | Preserve technical content unchanged | Review and import patch |
| `translate` | Apply controlled terminology mapping | Decode, validate, then review |
| `fixture` | Replace source prose with a representative example | Do not reverse-translate |
| `exclude` | Omit unnecessary or private material | None |
| `summarize` | Generate a technical contract from structured data | Treat as generated documentation |

Example configuration:

```yaml
version: 1

source_root: 'D:\AI\ComfyUI_portable\workshop'
view_root: '.local/views/current'

exclude:
  - '.git/**'
  - '.env'
  - '**/credentials*'
  - 'bamboozle/.local/**'
  - '**/outputs/**'

rules:
  - glob: 'scrape/**/*.py'
    policy: copy

  - glob: 'docs/**/*.md'
    policy: translate
    preserve:
      - fenced_code
      - inline_code
      - link_destinations

  - glob: 'templates/**/*.json'
    policy: fixture
    adapter: template_json

template_fields:
  preserve:
    - id
    - version
    - variables
    - constraints
    - compatibility
  translate:
    - category
    - tags
  fixture:
    - prompt
    - description
```

These field names are illustrative; the adapter must match your actual template schema.

**Default for unclassified files: omit and report.** Do not accidentally copy an entire repository into the view.

## 6. UDL: controlled terminology

UDL is the project’s **Unified Domain Lexicon**.

Map concepts to neutral technical terms rather than forcing an unrelated metaphor across the whole application.

Suitable categories include:

- Interaction type.
- Participant role.
- Contact region.
- Motion profile.
- Material effect.
- Scene condition.
- Compatibility constraint.

Choose the level of specificity necessary for the engineering task. A generic `interaction_type` may suffice for routing, but not for a physically accurate simulation.

### Entry structure

```yaml
version: 1

concepts:
  - id: category_017
    kind: category
    bam:
      canonical: "SOURCE_CATEGORY_LABEL"
    boozle:
      canonical: "interaction_profile_017"
    behavior:
      description: "Selects a template family."
      affects:
        - template_selection
        - compatibility_filtering
    reversible: true
```

For prose substitutions, define grammatical forms explicitly:

```yaml
  - id: operation_004
    kind: verb
    forms:
      - bam: "SOURCE_BASE_FORM"
        boozle: "actuate"
      - bam: "SOURCE_PAST_FORM"
        boozle: "actuated"
      - bam: "SOURCE_PROGRESSIVE_FORM"
        boozle: "actuating"
    reversible: true
```

These are schema examples, not a finished terminology dictionary. Building the real dictionary requires the actual repository vocabulary.

### Compiler requirements

The lexicon compiler must:

1. Reject duplicate target forms.
2. Reject ambiguous source forms in strict reversible mode.
3. Reject source/target overlap or explicitly resolve it before compilation.
4. Match complete terms, with language-appropriate boundaries.
5. Prefer longer phrases at the same position.
6. Replace once; never recursively process replacement output.
7. Preserve or explicitly record capitalization.
8. Fail on unsupported ambiguous mappings instead of guessing.

Do not use stemming or synonym generation in the reversible core.

## 7. Literal collision handling

Readable target words can already exist in the original text.

Before translation:

1. Escape any preexisting Bamboozle marker syntax.
2. Protect literal occurrences of reserved target terms.
3. Store protected values in the local manifest.
4. Translate source terminology.

Example:

```text
Original:
Check the actuator before applying SOURCE_BASE_FORM.

Neutral:
Check the actuator before applying actuate.
```

If `actuator` is also a reserved target, its original occurrence must be protected rather than later decoded accidentally.

For newly written literal target words, the development contract permits:

```text
⟦BZ:LITERAL:actuate⟧
```

Decoding restores literal escapes **after** reverse substitution.

**Limit:** no decoder can determine whether an unmarked new target word was intended literally. Reserve target vocabulary in translated prose and flag suspect edits for review.

## 8. Representative template fixtures

This is the most important feature for your coding-model workflow.

Replace unnecessary prose while preserving the features the code actually processes.

Example neutral fixture:

```json
{
  "id": "template_017",
  "version": 1,
  "category": "interaction_profile_017",
  "variables": ["actor_a", "actor_b", "duration"],
  "prompt": "{actor_a} passes a training object to {actor_b} over {duration} seconds.",
  "constraints": {
    "participant_count": 2,
    "requires_ordered_roles": true
  },
  "compatibility": {
    "motion_profile": "coordinated"
  }
}
```

The fixture should preserve relevant invariants:

- Placeholder names and multiplicity.
- Optional and required fields.
- Participant and role counts.
- Cross-template references.
- Constraint dependencies.
- Escaping and delimiter behavior.
- Multiline structure where relevant.
- Invalid examples and boundary conditions.

Do **not** claim a neutral fixture tests the original scene semantics. It tests the software contract.

If production logic relies on literal phrases inside prompt text, expose that dependency explicitly. Prefer migrating such logic to structured fields.

## 9. Generated context document

Create `TEMPLATE_CONTRACT.md` in the development view.

It should explain:

```markdown
# Template contract

Templates are domain-specific configuration records.

The examples in this workspace are neutral fixtures. They preserve
the schema and the stated processing invariants, not production prose.

The application:
- validates required variables;
- selects compatible template families;
- interpolates runtime values;
- submits assembled requests to the execution backend.

Stable template IDs must remain unchanged.
Fixture text must not replace production template text.
```

Include actual field definitions, call paths, and known failure cases extracted from the repository.

This is more useful than telling a model to “ignore semantics,” because it supplies the semantics needed for the engineering task.

## 10. Coding-model review prompt

```markdown
Review this development workspace for the requested engineering change.

Template examples are neutral fixtures. Their schema, placeholders,
identifiers, and declared constraints represent the production contract.

Task:
[Describe the concrete change.]

Review:
- parsing and validation;
- placeholder handling;
- compatibility logic;
- error handling;
- test coverage;
- integration with existing modules.

Keep stable identifiers unchanged.
Do not replace production templates with fixture text.
Preserve Bamboozle markers and controlled terminology.

If a missing domain detail affects correctness, identify the exact
field, behavior, or invariant needed rather than assuming it.

Return:
1. Findings.
2. Proposed patch.
3. Tests.
4. Unresolved assumptions.
```

This is a task-scoping prompt, not an instruction to bypass a model’s rules.

## 11. CLI workflow

These are proposed commands to implement—not commands available from an existing package.

```powershell
# Identify candidate terminology, files, and schema dependencies.
bamboozle inventory --config bamboozle.yaml

# Validate mappings and file policies.
bamboozle lint --config bamboozle.yaml

# Build the isolated development view and local manifest.
bamboozle build --config bamboozle.yaml

# Run schema, fixture, and transformation checks.
bamboozle verify --view .local\views\current

# After coding-model edits, inspect changes against the build baseline.
bamboozle diff --view .local\views\current

# Stage eligible changes without modifying the original repository.
bamboozle import --view .local\views\current --stage-only

# Apply reviewed changes only if source hashes still match.
bamboozle apply --reviewed
```

### Import rules

- `copy`: allow reviewed code changes.
- `translate`: reverse terminology, then validate and review.
- `fixture`: never overwrite production prose.
- `summarize`: never overwrite authoritative source automatically.
- Unknown files: require explicit approval.
- Changed source hashes: stop and require reconciliation.
- No automatic commits or destructive overwrites.

## 12. Implementation stack

Established options; these recommendations are not live-search-verified.

| Component | Tool |
| --- | --- |
| CLI | `Typer` or standard-library `argparse` |
| Configuration validation | `Pydantic` |
| YAML with comments preserved | `ruamel.yaml` |
| Template validation | `jsonschema` |
| Markdown tokenization | `markdown-it-py` |
| Small/medium terminology matcher | Python `re` |
| Large dictionary matching | `pyahocorasick` |
| Round-trip property tests | `Hypothesis` |
| Test runner | `pytest` |

Markdown tokenization alone does not guarantee byte-preserving edits. Use source-span-aware replacements or explicitly accept formatting changes.

General profanity dictionaries may help inventory candidate terms, but should not define the production mapping: they lack your project’s context and produce false positives.

## 13. Tests and acceptance criteria

| Area | Required result |
| --- | --- |
| Untouched translation | Decode reproduces original UTF-8 bytes |
| Target collisions | Original literal words survive |
| Marker collisions | Existing marker-like text survives |
| Phrases | Longest valid match wins |
| Replacement chains | Generated output is never reprocessed |
| Fixtures | Schema and declared invariants pass |
| Isolation | Excluded files and private manifests are absent |
| Edited terminology | Unsupported forms are reported |
| Patch import | Fixture prose cannot overwrite production prose |
| Source drift | Stale imports stop safely |

Track coding-model usefulness separately:

- Did it finish the requested technical review?
- Did it identify missing context accurately?
- Did the resulting patch pass tests?
- Did neutralization remove a necessary invariant?

Do not optimize only for “no refusal.” A confident but incorrect patch is a failed result.

## 14. Delivery sequence

1. **Workspace exporter:** explicit file policies, exclusions, manifests, baseline hashes.
2. **Template adapter:** neutral fixtures and generated technical contracts.
3. **UDL compiler:** reversible mappings, phrase matching, literal protection.
4. **Review/import pipeline:** staged patches and source-drift checks.
5. **Evaluation harness:** round-trip tests and representative engineering tasks.

**Start with the exporter and fixtures.** They directly address the point where your reviews break—template context—while preserving enough information for the coding model to do useful work. Add terminology translation where the documents genuinely need it, rather than making word replacement carry the entire system.

---
