# Protected paths and URLs in text translation

Use explicit protection when prose contains filenames, filesystem paths or URLs
that the application must consume unchanged. The shared document engine translates
surrounding vocabulary while preserving each recognized literal, including native
target words and marker-like characters inside it. Protected content remains
visible to the agent. This option does not encrypt or redact it.

For a dictionary containing `{"alpha":"omega","task":"stage"}`:

```text
Before: alpha task uses C:\models\alpha.bin and https://example.test/alpha
After:  omega stage uses C:\models\alpha.bin and https://example.test/alpha
```

```powershell
bamboozle --root . encode settings.txt --dict mapping.dictionary.json --format text --protect paths --protect urls -o settings_safe.txt
bamboozle --root . decode settings_safe.txt --strict -o exact.txt
```

Select either class alone or both. `--protect` requires `--format text`; duplicate
and unknown classes fail. Without this option, established whole-text behavior
continues. Existing Markdown, selected JSON/YAML, Python, and saved historical
operations retain their own rules.

The private per-file ledger stores the exact rule, dictionary, marker protocol,
literal values, original/encoded character offsets and document digests. Normal
decode accepts edits under that saved rule. Strict decode and verify require the
unchanged encoding and recover the exact original bytes. Do not replace a saved
rule by passing a different rule to decode.

For agent output saved under a new name, use the originating ledger with `decode
--ledger` or use reviewed restoration:

```powershell
bamboozle --root . restore-edits returned.txt --original settings.txt --ledger .settings_safe.txt.scm_ledger.json --terms translate -o restored.txt
# Apply the exact reviewed result using its reported digest:
bamboozle --root . restore-edits returned.txt --original settings.txt --ledger .settings_safe.txt.scm_ledger.json --terms translate -o restored.txt --apply --review-sha256 <review_sha256>
```

Use the actual ledger path reported by encode. Protected text restoration uses
`--terms translate`; line-based term decisions cannot override its span rules.
Missing-ledger recovery accepts the same `--format text --protect ...` selection
and requires the original file and dictionary. The compatibility `cipher.py` /
`python -m bamboozle.translate` file interface accepts the same options. Forward
stream conversion refuses to discard required private context; use file-to-file
conversion for protected text.

## Source agents and project views

Both private source-agent policies and version-2 project-view policies use this
same document selection:

```json
{
  "schema_version": 2,
  "files": {
    "settings.txt": {
      "policy": "translate",
      "document": {"format": "text", "protect": ["paths", "urls"]}
    }
  },
  "new_files": {
    "configs": {
      "policy": "translate",
      "document": {"format": "text", "protect": ["paths", "urls"]}
    }
  }
}
```

`serve-agent --policy source.agent-policy.json` applies protection on encoded reads,
exact edits, and decoded writes. Reads expose the selected classes in
`document_rules`; original span context stays private. Writes after reconnecting
must supply the read's source/context hashes. New files use their directory's
saved selection. Vandal routes these tools without implementing a second scanner.

Project views retain the same rules for existing and generated files. Review and
apply the returned multi-file application using its `review_sha256`; interrupted
publication resumes through the existing explicit recovery operation and its
retained context. Changed or removed literals produce
`protected_literal_changed`; reordered literals produce
`protected_literals_reordered`. These are review findings, since an intentional
configuration change can be legitimate. They do not reject every literal edit or
grant apply authority. An unchanged path containing original vocabulary is not
reported as untranslated prose.

Operator MCP `preview_file` accepts `document_format: "text"` and
`protect: ["paths", "urls"]`. `apply_preview`, reverse preview, restoration, and
project-view tools share the same engine and saved context. Source-agent tools
remain distinct from operator diffs and private decoding diagnostics.

## Recognition and limits

Recognition is lexical. It never checks filesystem existence, contacts a URL,
normalizes spelling, or interprets these strings as instructions.

| Class | Recognized spellings |
| --- | --- |
| Paths | Drive paths (`C:\models\alpha.bin`, `D:/alpha`), UNC paths, `/absolute`, `./relative`, `../parent`, `~/home`, relative components (`assets/alpha.csv`), and filenames with alphabetic extensions (`alpha.json`). |
| URLs | A scheme followed by `://`, `mailto:`, or `www.`, case-insensitively. Query strings and fragments remain intact. |
| Quoted literals | Matching single, double or backtick quotes on one line. Paths may contain spaces, including `"alpha folder/data.csv"`. URLs cannot contain whitespace. Quote delimiters are preserved. |

Unquoted spans end at whitespace, angle brackets, quotes, backticks or `|`.
Trailing prose `. , ; : ! ?` and unmatched closing brackets are excluded; balanced
parentheses remain part of a path/URL. Quote a literal whose final punctuation is
significant. Unquoted paths with spaces require quotes. Bare extensionless names,
arbitrary URI forms without `://` other than `mailto:`, escaped quote syntax and
multiline literal quoting are outside this grammar. Domains such as `alpha.test`
also look like filenames and are protected by the paths class. Choosing paths
alone does not implicitly protect portions of a URL.

Marker tokens in translated prose are parsed atomically; malformed markers there
fail. Marker-like filename components remain literal bytes. If a dictionary
substitution creates a new path/URL shape in ordinary prose and would break exact
reversibility, encoding refuses it. Choose an unambiguous target term or a document
selection appropriate to that content; no guessed inverse is used.

Each document is bounded by the existing byte limit, 1,000,000 analyzed characters
and 10,000 protected spans. Context entries have validated types, grammar, offsets,
ordering, bounds and digests. Historical rules without `protect` are unchanged.

`scripts/literals_smoke.py --workspace <new-directory>` exercises an application
that reads a CSV through a quoted path, validates an offline dispatch URL, and
writes/reads JSON through a marker-like path. CLI and actual operator/source-agent
stdio MCP workflows edit configuration, add a dependent statistics module and
generate configuration files. Neutral and restored applications execute 160
workloads per benchmark and reject four invalid datasets. The installed-wheel
qualifier repeats these flows outside the checkout.
