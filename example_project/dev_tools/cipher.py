"""Reversible, dictionary-based text substitution for local source files."""

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import MappingProxyType

LITERAL_PREFIX = "__SCM_LIT_"
LITERAL_TEMPLATE = LITERAL_PREFIX + "{}__"
LEDGER_FILE = ".scm_ledger.json"
LEDGER_VERSION = 2


class PublishedWriteError(OSError):
    """The destination exists, but removing its temporary link failed."""

    def __init__(self, file_path, cleanup_error):
        super().__init__(f"Temporary cleanup failed after saving {file_path}: {cleanup_error}")
        self.published_path = Path(file_path)


class PreparedOperation(Mapping):
    """Read-only operation inputs, detached ledger data, and a separate reporting view."""

    def __init__(self, **data):
        self._report = data["report"]
        self._initial_report = deepcopy(self._report)
        self._status = self._report["status"]
        data["mode"] = self._report["mode"]
        data["snapshot"] = MappingProxyType(dict(data["snapshot"]))
        if "ledger_data" in data:
            data["ledger_data"] = json.dumps(data["ledger_data"])
        self._data = MappingProxyType(data)

    def __getitem__(self, key):
        value = self._data[key]
        return json.loads(value) if key == "ledger_data" else value

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def _require_ready(self, expected):
        if self._status != expected:
            raise ValueError("Only an unsaved preview of the matching operation can be applied.")
        # Report decoration must never change publication paths, contents, or lifecycle.
        for key in ("error", "output_saved", "ledger_saved"):
            self._report.pop(key, None)
        self._report.update(deepcopy(self._initial_report))

    def _set_status(self, status, **fields):
        self._status = status
        self._report.update(status=status, **fields)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_json(file_path):
    with open(file_path, encoding="utf-8-sig") as source:
        return json.load(source, object_pairs_hook=_unique_pairs)


def write_text_atomic(text, file_path, overwrite=True):
    """Publish a complete UTF-8 file without truncating an existing destination."""
    file_path = Path(file_path)
    temporary_path = None
    published = False
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=file_path.parent,
            prefix=f".{file_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write(text)
        if overwrite:
            temporary_path.replace(file_path)
        else:
            # Same-directory hard linking publishes atomically without replacing a raced-in file.
            os.link(temporary_path, file_path)
        published = True
    finally:
        pending_error = sys.exc_info()[1]
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                if published:
                    raise PublishedWriteError(file_path, cleanup_error) from cleanup_error
                if pending_error is None:
                    raise
                if isinstance(pending_error, Exception):
                    raise OSError(
                        f"{pending_error}; temporary cleanup also failed: {cleanup_error}"
                    ) from pending_error
                # Preserve Ctrl+C/SystemExit instead of replacing them with a cleanup failure.


def save_json(data, file_path, overwrite=True):
    write_text_atomic(json.dumps(data, indent=2) + "\n", file_path, overwrite=overwrite)


def validate_dictionary(cipher_dict):
    if not isinstance(cipher_dict, dict):
        raise ValueError("Dictionary must be a JSON object mapping terms to terms.")
    for source, target in cipher_dict.items():
        for term in (source, target):
            if (
                not isinstance(term, str)
                or not term.isascii()
                or not term.islower()
                or term != term.lower()
                or term != term.strip()
            ):
                raise ValueError("Dictionary terms must be nonempty lowercase ASCII strings.")
            if LITERAL_PREFIX.lower() in term:
                raise ValueError(
                    f"Dictionary terms cannot contain reserved prefix {LITERAL_PREFIX}."
                )
    if len(set(cipher_dict.values())) != len(cipher_dict):
        raise ValueError("Dictionary targets must be unique for reversible conversion.")
    if set(cipher_dict) & set(cipher_dict.values()):
        raise ValueError("Dictionary source and target terms must not overlap.")


def match_case(original, target):
    if original.isupper():
        return target.upper()
    if original.istitle():
        return target.title()
    if original.islower():
        return target.lower()
    raise ValueError(f"Unsupported mixed-case term: {original!r}; use lower, upper, or title case.")


def _alternatives(terms):
    # The never-matching expression makes an empty dictionary an identity operation.
    return (
        "|".join(re.escape(term) for term in sorted(terms, key=lambda t: (-len(t), t))) or r"(?!)"
    )


def _term_pattern(terms, identifier_extras=""):
    # Underscores separate terms; Unicode letters/digits remain identifier characters.
    # ASCII case folding avoids matching e.g. a Kelvin sign as the letter K.
    boundary = r"[^\W_]"
    if identifier_extras:
        boundary = "(?:" + boundary + "|[" + re.escape(identifier_extras) + "])"
    return "(?<!" + boundary + ")(?ai:" + _alternatives(terms) + ")(?!" + boundary + ")"


def _literal_pattern(cipher_dict):
    return (
        re.escape(LITERAL_PREFIX)
        + "(?P<literal>(?ai:"
        + _alternatives(cipher_dict.values())
        + "))__"
    )


def _location(text, offset):
    line = (
        text.count("\n", 0, offset)
        + text.count("\r", 0, offset)
        - text.count("\r\n", 0, offset)
        + 1
    )
    column = offset - max(text.rfind("\n", 0, offset), text.rfind("\r", 0, offset))
    return f"line {line}, column {column}"


class CipherEngine:
    """Reusable matching rules with an immutable dictionary and per-call conversion counts."""

    def __init__(self, cipher_dict):
        validate_dictionary(cipher_dict)
        self.mapping = MappingProxyType(dict(cipher_dict))
        self.reverse_mapping = MappingProxyType({v: k for k, v in self.mapping.items()})
        self._literal = re.compile(_literal_pattern(self.mapping))
        self._prefix = re.compile(re.escape(LITERAL_PREFIX), re.IGNORECASE | re.ASCII)
        self._patterns = self._compile_patterns()

    def _compile_patterns(self, identifier_extras=""):
        return {
            "forward": re.compile(
                _term_pattern(set(self.mapping) | set(self.mapping.values()), identifier_extras)
            ),
            "reverse": re.compile(
                self._literal.pattern + "|" + _term_pattern(self.reverse_mapping, identifier_extras)
            ),
        }

    def _patterns_for(self, text):
        if not isinstance(text, str):
            raise ValueError("Text must be a string.")
        # Python's Unicode identifier continuations include combining marks and connector
        # punctuation that re's \w omits. Keep underscores as deliberate term separators.
        extras = (
            ""
            if text.isascii()
            else "".join(
                sorted(
                    char
                    for char in set(text)
                    if char != "_" and not char.isalnum() and ("a" + char).isidentifier()
                )
            )
        )
        return self._compile_patterns(extras) if extras else self._patterns

    def _substitute(self, text, mode, patterns, count_only=False):
        for prefix in self._prefix.finditer(text):
            if mode == "forward":
                raise ValueError(
                    f"Input contains reserved {LITERAL_PREFIX} markers at "
                    f"{_location(text, prefix.start())}; reverse it first."
                )
            if self._literal.match(text, prefix.start()) is None:
                raise ValueError(
                    f"Unrecognized or damaged literal marker at {_location(text, prefix.start())}."
                )
        counts = {"substitutions": 0, "literals": 0, "terms": {}}

        def replace(match):
            original = match.group(0)
            if mode == "forward":
                target = self.mapping.get(original.lower())
                if target is None:
                    counts["literals"] += 1
                    return original if count_only else LITERAL_TEMPLATE.format(original)
                term = original.lower()
            elif match.group("literal") is not None:
                counts["literals"] += 1
                return original if count_only else match.group("literal")
            else:
                term = target = self.reverse_mapping[original.lower()]
            counts["substitutions"] += 1
            counts["terms"][term] = counts["terms"].get(term, 0) + 1
            if count_only:
                return original
            try:
                return match_case(original, target)
            except ValueError as error:
                raise ValueError(f"{error} At {_location(text, match.start())}.") from error

        return patterns[mode].sub(replace, text), counts

    def transform(self, text, mode):
        if mode not in ("forward", "reverse"):
            raise ValueError(f"Unknown conversion mode: {mode}")
        patterns = self._patterns_for(text)
        result, counts = self._substitute(text, mode, patterns)
        if mode == "forward":
            restored, _ = self._substitute(result, "reverse", patterns)
            if restored != text:
                offset = next(
                    (i for i in range(min(len(text), len(restored))) if text[i] != restored[i]),
                    min(len(text), len(restored)),
                )
                raise ValueError(
                    f"Dictionary mapping is ambiguous at {_location(text, offset)}; "
                    "round trip would lose text."
                )
        return result, counts

    def count(self, text, mode):
        if mode == "verify":
            mode = "reverse"
        if mode not in ("forward", "reverse"):
            raise ValueError(f"Unknown conversion mode: {mode}")
        return self._substitute(text, mode, self._patterns_for(text), count_only=True)[1]


def process_forward(text, cipher_dict):
    return CipherEngine(cipher_dict).transform(text, "forward")[0]


def process_reverse(text, cipher_dict):
    return CipherEngine(cipher_dict).transform(text, "reverse")[0]


def ledger_path_for(encoded_path):
    return encoded_path.with_name(f".{encoded_path.name}{LEDGER_FILE}")


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dictionary_hash(cipher_dict):
    return text_hash(json.dumps(cipher_dict, sort_keys=True, separators=(",", ":")))


def conversion_counts(text, cipher_dict, mode):
    """Count original matches, without counting text introduced by replacements."""
    return CipherEngine(cipher_dict).count(text, mode)


def make_ledger(text, result, cipher_dict, input_path, output_path):
    return {
        "version": LEDGER_VERSION,
        "original_file": input_path.name,
        "encoded_file": output_path.name,
        "dictionary": cipher_dict,
        "dictionary_sha256": dictionary_hash(cipher_dict),
        "original_sha256": text_hash(text),
        "encoded_sha256": text_hash(result),
        "track_literals": sorted(
            {m.group("literal").lower() for m in re.finditer(_literal_pattern(cipher_dict), result)}
        ),
        # Keep the standalone script compatible with Python 3.8.
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),  # noqa: UP017
    }


def validate_ledger(ledger, cipher_dict, input_path):
    if (
        not isinstance(ledger, dict)
        or type(ledger.get("version")) is not int
        or ledger["version"] not in (1, LEDGER_VERSION)
        or ledger.get("encoded_file") != input_path.name
        or ledger.get("dictionary") != cipher_dict
    ):
        raise ValueError("Ledger does not match this file and dictionary; left untouched.")
    if ledger["version"] == LEDGER_VERSION:
        for field in ("dictionary_sha256", "original_sha256", "encoded_sha256"):
            value = ledger.get(field)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"Ledger has an invalid or missing {field}.")
        if ledger["dictionary_sha256"] != dictionary_hash(cipher_dict):
            raise ValueError("Ledger dictionary hash does not match the current dictionary.")


def reverse_integrity(text, result, ledger, strict=False):
    if ledger is None or ledger["version"] == 1:
        if strict:
            detail = (
                " If you have the original file and matching dictionary, "
                "use bamboozle recover ENCODED --original ORIGINAL to preview ledger recovery."
                if ledger is None
                else ""
            )
            raise ValueError(
                "Verification requires a version-2 ledger with integrity hashes." + detail
            )
        return "missing_ledger" if ledger is None else "legacy_ledger"
    if text_hash(text) != ledger["encoded_sha256"]:
        if strict:
            raise ValueError(
                "Encoded file has changed since the forward pass; verification failed."
            )
        return "edited"
    if text_hash(result) != ledger["original_sha256"]:
        raise ValueError("Restored text does not match the original hash recorded in the ledger.")
    return "unchanged"


def _diff_lines(text):
    # Compare exact physical lines; Unicode separators remain text content.
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$", text)


def preview_diff(original, result, input_path, output_path, context=3):
    if type(context) is not int or context < 0:
        raise ValueError("Diff context must be a nonnegative integer.")
    lines = difflib.unified_diff(
        _diff_lines(original),
        _diff_lines(result),
        fromfile=str(input_path),
        tofile=str(output_path),
        n=context,
    )
    displayed = []
    for line in lines:
        if line.endswith(("\r", "\n")):
            displayed.append(line.rstrip("\r\n") + "\n")
        else:
            displayed.append(line + "\n\\ No newline at end of file\n")
    return "".join(displayed)


def describe_counts(counts):
    substitutions = counts["substitutions"]
    literals = counts["literals"]
    terms = f"{substitutions} substitution{'s' if substitutions != 1 else ''}"
    native = f"{literals} native literal{'s' if literals != 1 else ''}"
    return f"{terms}, {native}"


def describe_dictionary(report):
    source = report["dictionary_source"]
    label = "saved in ledger" if source == "ledger" else source
    return f"Dictionary: {label} ({report['dictionary']})"


def nonnegative_int(value):
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a nonnegative integer") from error
    if number < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return number


def emit_report(report, as_json):
    if as_json:
        print(json.dumps(report, sort_keys=True))
        return
    status = report["status"]
    if status == "dictionary_valid":
        print(f"[*] Dictionary valid: {report['entries']} mappings ({report['dictionary']}).")
        return
    if "dictionary_source" in report:
        print(f"[*] {describe_dictionary(report)}")
    if report["mode"] == "recover":
        print(f"[*] Original: {report['original']}")
        print(f"[*] Encoded:  {report['input']}")
        print(f"[*] Exact encoded/original pair checked ({describe_counts(report['counts'])}).")
        if status == "recovered":
            print(f"[*] RECOVERED: Created {report['ledger']}. Source files were not rewritten.")
        else:
            print(f"[*] RECOVERY PREVIEW: Would create {report['ledger']}. No files written.")
            print("[*] Run recover again with --apply to create the missing ledger.")
        return
    if status == "verified":
        print(f"[*] VERIFIED: {report['input']}; encoded and restored hashes match the ledger.")
        return
    summary = describe_counts(report["counts"])
    if status == "preview":
        print(f"[*] PREVIEW: {report['output']} ({summary}). No files written.")
    else:
        print(f"[*] {report['mode'].upper()} PASS: Saved {report['output']} ({summary}).")
    if report.get("integrity") == "edited":
        print("[*] Input differs from the forward pass; restored edited text requires review.")
    elif report.get("integrity") in ("missing_ledger", "legacy_ledger"):
        print("[*] No version-2 integrity record: original byte hashes cannot be checked.")
    if report.get("diff"):
        print(report["diff"], end="")


def file_fingerprint(path):
    try:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except FileNotFoundError:
        return None


def check_snapshot(snapshot):
    for path, expected in snapshot.items():
        if file_fingerprint(path) != expected:
            raise ValueError(f"File changed since preview: {path}. Preview again before saving.")


def prepare_conversion(
    mode, input_path=None, output_path=None, dict_path=None, *, strict=False, keep_ledger=False
):
    """Prepare a frozen conversion and capture every file that could affect publication."""
    if mode not in ("forward", "reverse", "verify", "validate"):
        raise ValueError(f"Unknown conversion mode: {mode}")
    if mode != "validate" and input_path is None:
        raise ValueError("An input file is required for conversion and verification.")
    if mode == "validate" and input_path is not None:
        raise ValueError("Dictionary validation does not take an input file.")
    if mode in ("validate", "verify") and output_path is not None:
        raise ValueError("Read-only validation and verification do not take an output file.")
    if strict and mode not in ("reverse", "verify"):
        raise ValueError("Strict verification is available only when reversing or verifying.")
    if keep_ledger and mode != "reverse":
        raise ValueError("Keeping the ledger applies only to reverse conversion.")
    explicit_dictionary = dict_path is not None
    dict_path = Path(dict_path or Path(__file__).with_name("cipher_dict.json")).resolve()
    report = {"schema_version": 1, "mode": mode}
    snapshot = {}
    if mode == "validate":
        snapshot[dict_path] = file_fingerprint(dict_path)
        cipher_dict = load_json(dict_path)
        validate_dictionary(cipher_dict)
        check_snapshot(snapshot)
        report.update(
            status="dictionary_valid",
            dictionary=str(dict_path),
            entries=len(cipher_dict),
            dictionary_sha256=dictionary_hash(cipher_dict),
        )
        return PreparedOperation(report=report, snapshot=snapshot)

    input_path = Path(input_path).resolve()
    suffix = "_safe" if mode == "forward" else "_ready"
    output_path = (
        Path(output_path).resolve()
        if output_path
        else input_path.with_name(input_path.stem + suffix + input_path.suffix)
    )
    ledger_path = ledger_path_for(output_path if mode == "forward" else input_path)
    if mode != "verify" and (
        output_path in (dict_path, ledger_path_for(input_path))
        or ledger_path in (input_path, output_path, dict_path)
    ):
        raise ValueError("Input, output, dictionary, and ledger paths must not conflict.")
    if keep_ledger and output_path == input_path:
        raise ValueError("Cannot keep the encoded-file ledger when reversing in place.")
    watched_paths = {input_path, ledger_path, ledger_path_for(input_path)}
    if mode != "verify":
        watched_paths.add(output_path)
    destination_ledger = ledger_path_for(output_path)
    if mode == "reverse" and output_path != input_path:
        watched_paths.add(destination_ledger)
    for path in watched_paths:
        if path not in snapshot:
            snapshot[path] = file_fingerprint(path)

    if mode == "reverse" and output_path != input_path and snapshot[destination_ledger] is not None:
        raise ValueError(
            f"Destination has its own cipher ledger: {destination_ledger}. "
            "Choose another output path to preserve that encoded file and its metadata."
        )
    ledger_data = None
    if mode in ("reverse", "verify") and snapshot[ledger_path] is not None:
        ledger_data = load_json(ledger_path)
        if not isinstance(ledger_data, dict):
            raise ValueError("Ledger must be a JSON object; left untouched.")
    if ledger_data is not None and not explicit_dictionary:
        cipher_dict = ledger_data.get("dictionary")
        dictionary_source = "ledger"
        dictionary_path = ledger_path
    else:
        snapshot[dict_path] = file_fingerprint(dict_path)
        cipher_dict = load_json(dict_path)
        dictionary_source = "explicit" if explicit_dictionary else "default"
        dictionary_path = dict_path
    engine = CipherEngine(cipher_dict)
    if ledger_data is not None:
        validate_ledger(ledger_data, cipher_dict, input_path)
    with input_path.open(encoding="utf-8", newline="") as source:
        text = source.read()
    if mode == "forward":
        if snapshot[ledger_path_for(input_path)] is not None:
            raise ValueError("Input already has a cipher ledger; reverse it before encoding again.")
        result, counts = engine.transform(text, "forward")
        ledger_data = make_ledger(text, result, cipher_dict, input_path, output_path)
        integrity = "round_trip_checked"
    else:
        result, counts = engine.transform(text, "reverse")
        integrity = reverse_integrity(text, result, ledger_data, strict=strict or mode == "verify")

    check_snapshot(snapshot)
    report.update(
        status="verified" if mode == "verify" else "preview",
        input=str(input_path),
        ledger=str(ledger_path),
        dictionary=str(dictionary_path),
        dictionary_source=dictionary_source,
        dictionary_sha256=dictionary_hash(cipher_dict),
        integrity=integrity,
        input_sha256=text_hash(text),
        output_sha256=text_hash(result),
        input_bytes=len(text.encode("utf-8")),
        output_bytes=len(result.encode("utf-8")),
        counts=counts,
        changed=text != result,
    )
    if mode != "verify":
        report["output"] = str(output_path)
    return PreparedOperation(
        report=report,
        snapshot=snapshot,
        input_text=text,
        result=result,
        output_path=output_path,
        ledger_path=ledger_path,
        ledger_data=ledger_data,
        keep_ledger=keep_ledger,
    )


def prepare_recovery(encoded_path, original_path, dict_path=None):
    """Prove a supplied original reproduces the encoded bytes before recreating metadata."""
    encoded_path = Path(encoded_path).resolve()
    original_path = Path(original_path).resolve()
    if encoded_path == original_path:
        raise ValueError("Recovery requires separate original and encoded files.")
    conversion = prepare_conversion("forward", original_path, encoded_path, dict_path)
    ledger_path = conversion["ledger_path"]
    if conversion["snapshot"][ledger_path] is not None:
        raise FileExistsError(f"Ledger already exists: {ledger_path}. Recovery never replaces it.")
    if conversion["snapshot"][encoded_path] is None:
        raise FileNotFoundError(f"Encoded file does not exist: {encoded_path}")
    forward_report = conversion["report"]
    if conversion["snapshot"][encoded_path] != forward_report["output_sha256"]:
        raise ValueError(
            "Encoded file does not exactly match encoding the supplied original with this "
            "dictionary. Recovery requires the matching unedited pair; no files written."
        )
    ledger_data = conversion["ledger_data"]
    ledger_data["recovered"] = True
    report = {
        "schema_version": 1,
        "mode": "recover",
        "status": "recovery_preview",
        "input": str(encoded_path),
        "original": str(original_path),
        "ledger": str(ledger_path),
        "dictionary": forward_report["dictionary"],
        "dictionary_source": forward_report["dictionary_source"],
        "dictionary_sha256": forward_report["dictionary_sha256"],
        "original_sha256": forward_report["input_sha256"],
        "encoded_sha256": forward_report["output_sha256"],
        "original_bytes": forward_report["input_bytes"],
        "encoded_bytes": forward_report["output_bytes"],
        "counts": forward_report["counts"],
        "integrity": "original_pair_checked",
        "output_saved": False,
        "ledger_saved": False,
    }
    return PreparedOperation(
        report=report,
        snapshot=conversion["snapshot"],
        ledger_path=ledger_path,
        ledger_data=ledger_data,
    )


def apply_recovery(plan):
    """Publish only a missing ledger, preserving files and metadata created concurrently."""
    if not isinstance(plan, PreparedOperation):
        raise ValueError("Prepare a recovery preview before applying it.")
    plan._require_ready("recovery_preview")
    check_snapshot(plan["snapshot"])
    ledger_path = plan["ledger_path"]
    ledger_data = plan["ledger_data"]
    try:
        save_json(ledger_data, ledger_path, overwrite=False)
    except PublishedWriteError:
        plan._set_status("finalizing", ledger_saved=True)
        raise
    plan._set_status("finalizing", ledger_saved=True)
    # Detect changes during publication without deleting either party's files.
    final_snapshot = dict(plan["snapshot"])
    final_snapshot[ledger_path] = text_hash(json.dumps(ledger_data, indent=2) + "\n")
    check_snapshot(final_snapshot)
    plan._set_status("recovered")


def add_preview_diff(plan, context=3):
    report = plan["report"]
    report["diff"] = preview_diff(
        plan["input_text"], plan["result"], report["input"], report["output"], context=context
    )
    report["diff_context"] = context


def apply_conversion(plan, *, overwrite=True):
    """Save exactly the prepared text, rejecting stale source, mapping, ledger, or output."""
    if not isinstance(plan, PreparedOperation):
        raise ValueError("Prepare a conversion preview before applying it.")
    plan._require_ready("preview")
    mode = plan["mode"]
    check_snapshot(plan["snapshot"])
    output_path = plan["output_path"]
    ledger_path = plan["ledger_path"]
    if not overwrite:
        destinations = [output_path]
        if mode == "forward":
            destinations.append(ledger_path)
        for path in destinations:
            if plan["snapshot"][path] is not None:
                raise FileExistsError(
                    f"Destination already exists: {path}. Use --force to replace it."
                )
    try:
        if overwrite:
            write_text_atomic(plan["result"], output_path)
        else:
            write_text_atomic(plan["result"], output_path, overwrite=False)
    except PublishedWriteError:
        plan._set_status("finalizing", output_saved=True)
        raise
    # An interrupted finalization must not let this plan be applied a second time.
    plan._set_status("finalizing", output_saved=True)
    if mode == "forward" or (plan["ledger_data"] is not None and not plan["keep_ledger"]):
        check_snapshot({ledger_path: plan["snapshot"][ledger_path]})
    if mode == "forward":
        if overwrite:
            save_json(plan["ledger_data"], ledger_path)
        else:
            save_json(plan["ledger_data"], ledger_path, overwrite=False)
    elif plan["ledger_data"] is not None and not plan["keep_ledger"]:
        ledger_path.unlink()
    plan._set_status("saved")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Bamboozle Cipher — reversible local text conversion"
    )
    parser.add_argument(
        "--mode", choices=["forward", "reverse", "verify", "validate"], required=True
    )
    parser.add_argument("--input", type=Path, help="UTF-8 source file")
    parser.add_argument(
        "--output", type=Path, help="Destination (default: input stem + _safe or _ready)"
    )
    parser.add_argument(
        "--dict",
        type=Path,
        help="Mapping JSON (default: saved ledger on reverse/verify, otherwise bundled dictionary)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    parser.add_argument(
        "--diff", action="store_true", help="Show a unified diff; implies --dry-run"
    )
    parser.add_argument(
        "--context",
        type=nonnegative_int,
        help="Show a diff with N context lines; implies --dry-run",
    )
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report")
    parser.add_argument(
        "--strict", action="store_true", help="Reverse only a verified, unchanged file"
    )
    parser.add_argument(
        "--keep-ledger", action="store_true", help="Keep the ledger after reversing"
    )
    args = parser.parse_args(argv)
    if args.mode != "validate" and args.input is None:
        parser.error("--input is required for forward, reverse, and verify")
    if args.mode in ("validate", "verify") and (
        args.output or args.diff or args.dry_run or args.context is not None
    ):
        parser.error("--output, --diff, --context, and --dry-run apply only to forward and reverse")
    if args.mode == "validate" and args.input:
        parser.error("--input does not apply to dictionary validation")
    if args.mode != "reverse" and (args.strict or args.keep_ledger):
        parser.error("--strict and --keep-ledger apply only to reverse")

    report = {"schema_version": 1, "mode": args.mode}
    try:
        plan = prepare_conversion(
            args.mode,
            args.input,
            args.output,
            args.dict,
            strict=args.strict,
            keep_ledger=args.keep_ledger,
        )
        report = plan["report"]
        if args.mode in ("forward", "reverse"):
            if args.diff or args.context is not None:
                add_preview_diff(plan, context=3 if args.context is None else args.context)
            if not (args.dry_run or args.diff or args.context is not None):
                apply_conversion(plan)
        emit_report(report, args.json)
        return 0
    except (OSError, UnicodeError, ValueError) as error:
        output_saved = report.get("output_saved", False)
        if args.json:
            report.update(status="error", error=str(error), output_saved=output_saved)
            emit_report(report, True)
        elif output_saved:
            print(
                f"[!] Output saved to {report['output']}, but finalization failed: {error}",
                file=sys.stderr,
            )
        else:
            print(f"[!] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
