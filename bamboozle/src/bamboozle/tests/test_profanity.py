"""Real vocabulary, reversible case handling and complete source/agent workflows."""

import asyncio
import json
import subprocess
import sys
from importlib.resources import files

import pytest
from fastmcp import Client

from bamboozle.agent_files import AgentFiles
from bamboozle.cli import main
from bamboozle.dictionary import Dictionary
from bamboozle.engine import Translator
from bamboozle.mcp import create_server
from bamboozle.restoration import prepare_restore, verify_restoration
from bamboozle.service import prepare, scan, verify
from bamboozle.utils import sha256
from bamboozle.workspace import Workspace


@pytest.fixture(scope="module")
def translator():
    return Translator(Dictionary.load())


def test_original_vocabulary_is_preserved_and_profiles_are_versioned():
    original_bytes = files("bamboozle").joinpath("cipher_dict.json").read_bytes()
    original = json.loads(original_bytes)
    # This is the shipped 110-entry vocabulary, not a newly reconstructed subset.
    assert (
        Dictionary(original).fingerprint
        == "cb5e2d56e3a6a1a01cae27cb8f192297f4b841d4972b09b91feefafd66e3e11e"
    )
    combined = Dictionary.load()
    profanity = Dictionary.load(profile="profanity")
    assert len(combined) == 415 and len(profanity) == 310
    assert all(combined[word] == target for word, target in original.items())
    assert all(combined[word] == target for word, target in profanity.items())
    assert combined.version == "2" and profanity.version == "profanity-1"
    assert "penetration" in combined and "penetration" not in profanity
    assert "fuck" in profanity and "asshole" in profanity and "wtf" in profanity


@pytest.mark.parametrize("profile", ["default", "profanity"])
def test_every_bundled_term_and_native_literal_roundtrips_in_all_case_forms(profile):
    dictionary = Dictionary.load(profile=profile)
    engine = Translator(dictionary)
    for source, target in dictionary.items():
        mixed = "".join(
            char.upper() if index % 2 else char for index, char in enumerate(source)
        )
        for original in (
            source,
            source.upper(),
            source.title(),
            source.capitalize(),
            mixed,
        ):
            encoded = engine.transform(original)
            assert encoded.substitutions == 1, original
            assert encoded.text != original, original
            assert engine.reverse(encoded.text) == original, original
        for original in (target, target.upper(), target.title()):
            protected = engine.transform(original)
            assert protected.literals == 1, original
            assert engine.reverse(protected.text) == original, original


@pytest.mark.parametrize(
    "text",
    [
        "class assignment assistant assessment assets assemble bass classic passage",
        "Scunthorpe Dickinson cockpit cocktail peacock shell hello skill skillful",
        "shitake bullshitake fucksake bitchin hellenic prickly twattle crape",
        "ass_count fuck_count motherfucker2 αfuck fuck\u0301 \u0301fuck",
    ],
)
def test_unrelated_words_and_unicode_identifiers_are_not_partially_filtered(
    translator, text
):
    assert translator.forward(text) == text
    assert translator.scan(text)["matched_terms"] == 0


def test_longest_phrases_variants_and_possessives_keep_exact_meaning(translator):
    text = "Motherfucker's! for fuck's sake; Son of a bitch. damn it; mother-fucking; shitheads; goddammit"
    result = translator.transform(text)
    assert result.substitutions == 7
    assert "Hostile_descendant_label" in result.text
    assert "ancestor_excitation_hyphen" in result.text
    assert translator.reverse(result.text) == text


def test_profanity_profile_leaves_nonprofane_domain_terms_alone():
    text = "penetration wet facial cnc dp atm swallow"
    assert Translator(Dictionary.load(profile="profanity")).forward(text) == text


def test_explicit_disguised_spellings_and_curly_apostrophes_are_lossless(translator):
    text = "F**K; sh!t; b1tch; a$$hole; for fuck’s sake; Bitch’s; f.u.c.k; f u c k"
    result = translator.transform(text)
    assert result.substitutions == 8
    assert translator.reverse(result.text) == text


def test_compiled_patterns_are_reused_for_repeated_scans(translator):
    pattern = translator._pattern("fuck", "forward")
    assert translator._pattern("shit", "forward") is pattern
    # Combining marks require an extended identifier boundary, not a different vocabulary.
    assert translator._pattern("fuck\u0301", "forward") is not pattern


def test_case_marker_is_source_free_and_survives_generated_code(translator):
    encoded = translator.forward("fUcK")
    assert encoded == "__SCM_CASE_a_kinetic_impulse__"
    assert "fuck" not in encoded.casefold()
    assert encoded.isidentifier()
    assert translator.reverse(f"{encoded}(4)") == "fUcK(4)"


@pytest.mark.parametrize(
    "text",
    [
        "__SCM_CASE_a_unknown__",
        "__SCM_CASE_a_kinetic_impulse",
        "__SCM_CASE_10_kinetic_impulse__",
        "__SCM_CASE_a_KINETIC_IMPULSE__",
        "__SCM_CASE_zz_kinetic_impulse__",
        "__SCM_CASE_",
        "__scm_case_" + "f" * 129 + "_kinetic_impulse__",
    ],
)
def test_damaged_case_markers_fail_explicitly(translator, text):
    with pytest.raises(ValueError, match="[Cc]ase marker"):
        translator.reverse(text)
    with pytest.raises(ValueError, match="reserved"):
        translator.forward(text)


def test_case_mask_cannot_select_punctuation_or_lossy_unicode():
    with pytest.raises(ValueError, match="non-reversible"):
        Translator({"a-b": "neutral"}).reverse("__SCM_CASE_2_neutral__")
    with pytest.raises(ValueError, match="non-reversible"):
        Translator({"straße": "neutral"}).reverse("__SCM_CASE_10_neutral__")
    engine = Translator({"élan": "énergie", "straße": "straße_alt"})
    for text in ("éLaN", "Élan", "straße", "Straße"):
        assert engine.reverse(engine.forward(text)) == text


def test_reserved_case_prefix_is_not_a_dictionary_term():
    with pytest.raises(ValueError, match="reserved"):
        Dictionary({"word": "__scm_case_a_neutral__"})


def test_profile_selection_rejects_unknown_or_ambiguous_input(tmp_path):
    with pytest.raises(ValueError, match="Unknown"):
        Dictionary.load(profile="unknown")
    path = tmp_path / "custom.json"
    path.write_text('{"alpha":"beta"}', encoding="utf-8")
    with pytest.raises(ValueError, match="either"):
        Dictionary.load(path, profile="profanity")


def test_profile_resource_is_private_to_agent_file_tools(tmp_path):
    path = tmp_path / "profanity_dict.json"
    path.write_bytes(files("bamboozle").joinpath("profanity_dict.json").read_bytes())
    files_api = AgentFiles(Workspace(tmp_path), Dictionary.load(profile="profanity"))
    with pytest.raises(ValueError, match="Private"):
        files_api.read(path.name)
    with pytest.raises(ValueError, match="Private"):
        files_api.write(path.name, "{}")


def test_read_only_scan_has_exact_offsets_counts_and_bounded_findings(tmp_path):
    text = "🙂 fUcK\r\nSon of a bitch!\rshit\nFUCK fuck"
    path = tmp_path / "source.txt"
    original = text.encode("utf-8")
    path.write_bytes(original)
    report = scan(
        Workspace(tmp_path),
        "source.txt",
        Dictionary.load(profile="profanity"),
        max_matches=3,
    )
    assert report["matched_terms"] == 5
    assert report["counts"] == {"fuck": 3, "shit": 1, "son of a bitch": 1}
    assert not report["matches_complete"] and not report["output_saved"]
    assert [(row["line"], row["column"]) for row in report["matches"]] == [
        (1, 3),
        (2, 1),
        (3, 1),
    ]
    for row in report["matches"]:
        assert text[row["start"] : row["end"]] == row["term"]
    assert report["input_sha256"] == sha256(original)
    assert path.read_bytes() == original and list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("limit", [0, -1, 10001, True])
def test_invalid_scan_limits(translator, limit):
    with pytest.raises(ValueError, match="max_matches"):
        translator.scan("fuck", max_matches=limit)


def test_expansion_limit_and_original_marker_handling():
    with pytest.raises(ValueError, match="size limit"):
        Translator({"fuck": "neutral"}, max_output_chars=10).forward("fUcK")
    engine = Translator({"fuck": "neutral"})
    assert engine.reverse("__SCM_LIT_nEuTrAl__") == "nEuTrAl"
    with pytest.raises(ValueError, match="literal marker"):
        engine.reverse("__SCM_LIT_unknown__")


def test_cli_scan_and_profile_conversion_use_saved_dictionary(tmp_path, capsys):
    original = b"fUcK, shitheads! penetration\r\n"
    (tmp_path / "source.txt").write_bytes(original)
    common = ["--root", str(tmp_path)]
    assert main([*common, "scan", "source.txt", "--profile", "profanity"]) == 0
    assert json.loads(capsys.readouterr().out)["matched_terms"] == 2
    assert main([*common, "encode", "source.txt", "--profile", "profanity"]) == 0
    capsys.readouterr()
    assert b"penetration" in (tmp_path / "source_safe.txt").read_bytes()
    assert main([*common, "decode", "source_safe.txt", "--strict"]) == 0
    capsys.readouterr()
    assert (tmp_path / "source_safe_ready.txt").read_bytes() == original
    assert (
        main(
            [
                *common,
                "decode",
                "source_safe.txt",
                "--profile",
                "default",
                "-o",
                "wrong.txt",
            ]
        )
        == 1
    )
    assert "does not match" in capsys.readouterr().out
    assert not (tmp_path / "wrong.txt").exists()


def test_old_mapping_ledger_still_verifies_after_default_expansion(tmp_path):
    old = Dictionary.from_document(
        json.loads(files("bamboozle").joinpath("cipher_dict.json").read_bytes())
    )
    original = b"cum fuck\r\n"
    (tmp_path / "source.txt").write_bytes(original)
    workspace = Workspace(tmp_path)
    prepare(workspace, "source.txt", dictionary=old).apply()
    assert verify(workspace, "source_safe.txt")["status"] == "verified"
    decoded = prepare(workspace, "source_safe.txt", mode="reverse", strict=True)
    assert decoded.result == original


def test_mixed_case_agent_edit_and_generated_dependent_module_execute(tmp_path):
    original = b"def fUcK(value):\r\n    return value + 1\r\nassert fUcK(3) == 4\r\n"
    (tmp_path / "node.py").write_bytes(original)
    mapping = Dictionary.load(profile="profanity")
    files_api = AgentFiles(Workspace(tmp_path), mapping)
    read = files_api.read("node.py")
    assert "fuck" not in read["content"].casefold()
    assert "__SCM_CASE_" in read["content"]
    edited = read["content"].replace("value + 1", "value * 2").replace("== 4", "== 6")
    result = files_api.write("node.py", edited)
    identifier = Translator(mapping).forward("fUcK")
    files_api.write(
        "generated.py", f"from node import {identifier}\nassert {identifier}(5) == 10\n"
    )
    subprocess.run(
        [sys.executable, str(tmp_path / "generated.py")], check=True, timeout=10
    )
    assert (tmp_path / result["history"] / "original.bin").read_bytes() == original
    assert b"fUcK" in (tmp_path / "node.py").read_bytes()


def test_reviewed_restoration_preserves_case_markers_on_changed_lines(tmp_path):
    original = b"def fUcK(value):\n    return value + 1\n"
    (tmp_path / "node.py").write_bytes(original)
    workspace = Workspace(tmp_path)
    prepare(workspace, "node.py").apply()
    safe = tmp_path / "node_safe.py"
    safe.write_bytes(safe.read_bytes().replace(b"(value)", b"(value=2)"))
    plan = prepare_restore(workspace, "node_safe.py", "node.py")
    assert plan.result == original.replace(b"(value)", b"(value=2)")
    plan.apply(plan.review_sha256)
    assert verify_restoration(workspace, plan.output)["status"] == "verified"


def test_operator_mcp_scan_uses_configured_profile_and_stays_read_only(tmp_path):
    (tmp_path / "source.txt").write_text("fUcK penetration shit", encoding="utf-8")
    server = create_server(tmp_path, Dictionary.load(profile="profanity"))

    async def exercise():
        async with Client(server) as client:
            result = await client.call_tool(
                "scan_file", {"source": "source.txt", "max_matches": 1}
            )
            content = result.content[0]
            assert content.type == "text"
            report = json.loads(content.text)
            assert report["matched_terms"] == 2 and len(report["matches"]) == 1
            assert not report["output_saved"]

    asyncio.run(exercise())
    assert len(list(tmp_path.iterdir())) == 1
