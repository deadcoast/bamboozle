# Recursive JSON value translation

`--json-values-only` explicitly selects every string value in a JSON document,
including nested objects, arrays, and a root string. Translation preserves keys,
numbers, booleans, nulls, whitespace, BOMs and line endings. It edits string token
spans rather than serializing the whole document.

With `{"alpha":"omega","task":"stage"}` as the dictionary:

```json
{"alpha":"alpha", "nested":[{"message":"task"}], "count":1e+03}
```

becomes:

```json
{"alpha":"omega", "nested":[{"message":"stage"}], "count":1e+03}
```

```powershell
bamboozle --root . encode config.json --dict mapping.dictionary.json --json-values-only -o config_safe.json
bamboozle --root . verify config_safe.json
bamboozle --root . decode config_safe.json --strict -o config_exact.json
```

The flag explicitly selects JSON, so `--format json` is optional. Mixing it with
exact `--json-pointer` selections or another format fails. The existing pointer
mode is unchanged and remains suitable when only particular values should be
translated. Recursive selection includes identifier, URL and path string values;
use exact pointers to keep other values outside translation. Plain-text `protect`
rules are a separate explicit adapter option.

## Agent and project policies

Source-agent and version-2 project-view policies share the product rule:

```json
{
  "schema_version": 2,
  "files": {
    "config.json": {
      "policy": "translate",
      "document": {"format": "json", "values_only": true}
    },
    "records.json": {
      "policy": "translate",
      "document": {"format": "json", "values_only": true}
    }
  },
  "new_files": {
    "generated": {
      "policy": "translate",
      "document": {"format": "json", "values_only": true}
    }
  }
}
```

`serve-agent --policy source.agent-policy.json` applies the same rule to reads,
exact edits and complete writes. Keep the read's source/context hashes for writes
after reconnecting. Generated JSON files follow their directory's policy. Vandal
reports the public rule and routes these tools; the product owns translation.

Project views save the rule with the private mapping and spelling context.
Returned new string fields and array elements are decoded recursively under that
rule. Keys and nonstring changes remain deliberate application edits and appear
in operator `protected_json_changed` findings. Renaming a string-valued key or
changing its type is detected even though the string contents are translated.
Review the complete application change and apply its exact review digest. After
interrupted publication, recover the retained import with `view-apply --recover`;
do not replace its original rules or regenerate intent.

Operator MCP `preview_file` accepts `json_values_only: true` and returns a plan
for `apply_preview`. Reverse preview uses `reverse: true`; ordinary decode uses
saved rules automatically. The compatibility `cipher.py` and
`python -m bamboozle.translate` file interfaces accept `--json-values-only` too.

## Spelling, collisions and recovery

The private ledger retains original/encoded spellings for changed string tokens.
An unchanged encoding restores the exact source bytes, including `\u0061` escapes
and numeric spellings. During edited restoration, unchanged encoded tokens at
their original pointers recover their original spelling. If array insertion or
other edits move a token, recursive mode also restores its spelling when all saved
occurrences of that encoded token agree. When moved occurrences had conflicting
original spellings, normal JSON rendering preserves the decoded value without
guessing which original node moved. Historical exact-pointer rules retain their
location-based spelling contract.

Existing literal/case/prefix-escape markers remain atomic. Original occurrences
of neutral vocabulary receive the normal literal protection. Newly added unmarked
neutral words are decoded as vocabulary; preserve markers for intended literals.
Private context is never part of the neutral agent policy or returned content.

For newly named output, select the original ledger explicitly. Reviewed restoration
uses the same adapter:

```powershell
bamboozle --root . restore-edits returned.json --original config.json --ledger .config_safe.json.scm_ledger.json --terms translate -o restored.json
bamboozle --root . restore-edits returned.json --original config.json --ledger .config_safe.json.scm_ledger.json --terms translate -o restored.json --apply --review-sha256 <review_sha256>
```

Missing-ledger recovery uses `recover` with the original file, dictionary and
`--json-values-only`; it must reproduce the exact encoded bytes before writing
metadata. Forward stream translation refuses to discard required spelling context;
use file-to-file encoding when context is produced.

## Bounds and application qualification

Recursive selection permits at most 10,000 string values, depth 64, 100,000 JSON
value nodes, 2,048 characters per decoded pointer and 1,000,000 total selected
pointer characters, within the existing document byte limit. Empty arrays/objects
and documents with no string values are valid no-ops. Duplicate keys, nonfinite
numbers, malformed JSON/markers, incompatible contexts and exceeded bounds fail
before publication. Decoded recursive pointers and selected strings must be UTF-8
representable; escaped unpaired surrogates are refused. Exact-pointer limits and
old saved operations are unchanged.

`scripts/json_values_smoke.py --workspace <new-directory>` runs a complete batching
application with nested configuration and a 300-record JSON dataset containing
900 strings. Through CLI and actual operator/source-agent stdio MCP, it adds a
route and record, inserts array values, generates a configuration, and adds a
dependent summary module. Neutral/restored applications execute 56 workload cases
per benchmark and reject four invalid datasets. Each flow injects an interrupted
multi-file import, resumes its saved rules/context, and executes the recovered
application. Installed-wheel qualification repeats those workflows outside the
source checkout.
