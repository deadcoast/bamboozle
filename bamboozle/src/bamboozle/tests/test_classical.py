"""Lossless algorithms, private operator recipes and complete reviewed operations."""

import asyncio
import json
import random
import subprocess
import sys
from typing import Any, cast

import pytest
from fastmcp import Client

from bamboozle import restoration
from bamboozle.agent_files import AgentFiles
from bamboozle.cipher_operations import (
    capabilities,
    prepare_transform,
    verify_transform,
)
from bamboozle.classical import REGISTRY, Pipeline
from bamboozle.dictionary import Dictionary
from bamboozle.mcp import create_server
from bamboozle.scm_mcp import create_server as agent_server
from bamboozle.service import OperationError
from bamboozle.utils import json_bytes
from bamboozle.workspace import Workspace

PARAMS = {
    "caesar": {"shift": -9223372036854775808},
    "rot13": {},
    "atbash": {},
    "affine": {"a": -5, "b": 9223372036854775807},
    "scytale": {"width": 13},
    "polybius-square": {},
    "vigenere": {"key": "LEMON"},
    "beaufort": {"key": "FORTIFICATION"},
    "gronsfeld": {"key": "00131415"},
    "rail-fence": {"rails": 13},
    "columnar-transposition": {"key": "BALLOON"},
}


def recipe(*names, params=None):
    return {
        "schema_version": 1,
        "steps": [
            {
                "cipher": name,
                "variant": REGISTRY[name].variant,
                "params": params if params is not None else dict(PARAMS[name]),
            }
            for name in names
        ],
    }


@pytest.mark.parametrize("name", list(PARAMS))
def test_all_byte_values_variable_lengths_and_repeated_keys_are_exact(name):
    rng = random.Random(701)
    pipeline = Pipeline(recipe(name))
    corpus = [b"", bytes(range(256)), b"~_\x00" * 300, "I/J naïve 🍂\r\n".encode()]
    corpus += [rng.randbytes(size) for size in range(515)]
    for raw in corpus:
        encoded = pipeline.transform(raw)
        assert pipeline.transform(encoded, reverse=True) == raw
        if name != "polybius-square":
            assert len(encoded) == len(raw)
    assert pipeline.document() == recipe(name)


@pytest.mark.parametrize(
    ("name", "params", "text", "expected"),
    [
        ("caesar", {"shift": 3}, b"Attack, Zebra!", b"Dwwdfn, Cheud!"),
        ("rot13", {}, b"Hello, Zebra!", b"Uryyb, Mroen!"),
        ("atbash", {}, b"abcXYZ", b"zyxCBA"),
        ("affine", {"a": 5, "b": 8}, b"Affine Cipher", b"Ihhwvc Swfrcp"),
        ("vigenere", {"key": "LEMON"}, b"ATTACK AT DAWN", b"LXFOPV EF RNHR"),
        ("beaufort", {"key": "B"}, b"ABCXYZ", b"BAZEDC"),
        ("gronsfeld", {"key": "123"}, b"ABC XYZ", b"BDF YAC"),
        ("scytale", {"width": 3}, b"ABCDE", b"ADBEC"),
        ("columnar-transposition", {"key": "CAB"}, b"ABCDEF", b"BECFAD"),
        (
            "rail-fence",
            {"rails": 3},
            b"WEAREDISCOVEREDFLEEATONCE",
            b"WECRLTEERDSOEEFEAOCAIVDEN",
        ),
        ("polybius-square", {}, b"AjjI 1~\x00", b"u11x6ax6au24x20x31x7ex00"),
    ],
)
def test_independent_known_answers(name, params, text, expected):
    pipeline = Pipeline(recipe(name, params=params))
    assert pipeline.transform(text) == expected
    assert pipeline.transform(expected, reverse=True) == text


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("caesar", {"shift": True}),
        ("caesar", {"shift": 2**63}),
        ("caesar", {"shift": 1, "key": "x"}),
        ("caesar", {}),
        ("affine", {"a": 13, "b": 1}),
        ("affine", {"a": 0, "b": 0}),
        ("affine", {"a": 5, "b": None}),
        ("rot13", {"shift": 3}),
        ("scytale", {"width": 0}),
        ("rail-fence", {"rails": -1}),
        ("vigenere", {"key": ""}),
        ("vigenere", {"key": "L E M O N"}),
        ("vigenere", {"key": "é"}),
        ("beaufort", {"key": "A" * 129}),
        ("gronsfeld", {"key": "١٢"}),
        ("gronsfeld", {"key": 123}),
        ("columnar-transposition", {"key": "\x80"}),
        ("columnar-transposition", {"key": ""}),
        ("polybius-square", []),
    ],
)
def test_invalid_parameters_fail_before_processing(name, params):
    with pytest.raises((ValueError, TypeError)):
        Pipeline(recipe(name, params=params))


def test_pipeline_direction_pinning_limits_and_immutable_recipe():
    document = recipe(
        "vigenere",
        "scytale",
        "polybius-square",
        "columnar-transposition",
        "rail-fence",
        "affine",
    )
    pipeline = Pipeline(document)
    digest = pipeline.fingerprint
    document["steps"][0]["params"]["key"] = "OTHER"
    assert pipeline.fingerprint == digest
    raw = bytes(range(256)) * 19
    assert pipeline.transform(pipeline.transform(raw), reverse=True) == raw
    with pytest.raises(ValueError, match="expansion"):
        pipeline.transform(b"abcd", limit=10)
    with pytest.raises(ValueError, match="input"):
        pipeline.transform(b"abcd", limit=3)
    with pytest.raises(TypeError):
        pipeline.transform(b"a", reverse=cast(Any, "false"))
    for name, key in (("scytale", "width"), ("rail-fence", "rails")):
        assert (
            Pipeline(recipe(name, params={key: 2**63 - 1})).transform(b"short")
            == b"short"
        )


@pytest.mark.parametrize(
    "raw", [b"l1", b"l66", b"u00", b"xzz", b"x4A", b"x41", b"x61", b"a11", b"11 "]
)
def test_polybius_rejects_malformed_and_noncanonical_frames(raw):
    with pytest.raises(ValueError, match="Polybius"):
        Pipeline(recipe("polybius-square")).transform(raw, reverse=True)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row.update(schema_version=True),
        lambda row: row.update(steps=[]),
        lambda row: row.update(steps=row["steps"] * 9),
        lambda row: row["steps"][0].update(variant="unversioned"),
        lambda row: row["steps"][0].update(cipher="AES"),
        lambda row: row.update(unknown=True),
    ],
)
def test_recipe_cannot_infer_variants_or_drop_unknown_fields(mutate):
    row = recipe("caesar")
    mutate(row)
    with pytest.raises(ValueError):
        Pipeline(row)


def setup_files(tmp_path):
    workspace = Workspace(tmp_path)
    raw = bytes(range(256)) * 3 + b"\x00tail~_"
    (tmp_path / "payload.bin").write_bytes(raw)
    (tmp_path / "transfer.cipher-recipe.json").write_bytes(
        json_bytes(
            recipe(
                "vigenere", "polybius-square", "columnar-transposition", "rail-fence"
            )
        )
    )
    return workspace, raw


def test_reviewed_binary_forward_reverse_and_saved_snapshot_verification(tmp_path):
    workspace, raw = setup_files(tmp_path)
    plan = prepare_transform(
        workspace, "payload.bin", "transfer.cipher-recipe.json", "encoded.bin"
    )
    assert not (tmp_path / "encoded.bin").exists()
    assert "LEMON" not in json.dumps(plan.report())
    plan.apply(plan.review_sha256)
    assert verify_transform(workspace, "encoded.bin")["inputs"] == {
        "source": "unchanged",
        "recipe_path": "unchanged",
    }
    restored = prepare_transform(
        workspace,
        "encoded.bin",
        "transfer.cipher-recipe.json",
        "restored.bin",
        reverse=True,
    )
    restored.apply(restored.review_sha256)
    assert (tmp_path / "restored.bin").read_bytes() == raw
    assert verify_transform(workspace, "restored.bin")["exact_inverse_checked"]
    (tmp_path / "payload.bin").write_bytes(b"changed")
    (tmp_path / "transfer.cipher-recipe.json").write_bytes(b"changed")
    assert verify_transform(workspace, "encoded.bin")["inputs"] == {
        "source": "changed",
        "recipe_path": "changed",
    }
    (tmp_path / "payload.bin").unlink()
    assert verify_transform(workspace, "encoded.bin")["inputs"]["source"] == "missing"


@pytest.mark.parametrize("changed", ["payload.bin", "transfer.cipher-recipe.json"])
def test_stale_previews_refuse_publication(tmp_path, changed):
    workspace, _ = setup_files(tmp_path)
    plan = prepare_transform(
        workspace, "payload.bin", "transfer.cipher-recipe.json", "encoded.bin"
    )
    (tmp_path / changed).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed after review"):
        plan.apply(plan.review_sha256)
    assert not (tmp_path / "encoded.bin").exists()


def test_partial_publication_retains_bytes_and_requires_explicit_recovery(
    tmp_path, monkeypatch
):
    workspace, _ = setup_files(tmp_path)
    plan = prepare_transform(
        workspace, "payload.bin", "transfer.cipher-recipe.json", "encoded.bin"
    )
    publish = restoration.atomic_write

    def fail_receipt(path, data, **kwargs):
        if path.name.endswith(".cipher-receipt.json"):
            raise OSError("injected receipt publication failure")
        return publish(path, data, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(restoration, "atomic_write", fail_receipt)
        with pytest.raises(OperationError) as failure:
            plan.apply(plan.review_sha256)
    assert (
        failure.value.report["output_saved"]
        and not failure.value.report["metadata_saved"]
    )
    assert (tmp_path / "encoded.bin").read_bytes() == plan.result
    with pytest.raises(FileExistsError):
        prepare_transform(
            workspace, "payload.bin", "transfer.cipher-recipe.json", "encoded.bin"
        )
    recovered = prepare_transform(
        workspace,
        "payload.bin",
        "transfer.cipher-recipe.json",
        "encoded.bin",
        recover_metadata=True,
    )
    assert recovered.review_sha256 == plan.review_sha256
    assert (
        recovered.apply(plan.review_sha256, recover_metadata=True)["status"]
        == "metadata_recovered"
    )
    assert verify_transform(workspace, "encoded.bin")["status"] == "verified"


@pytest.mark.parametrize(
    "name", ["private.cipher-recipe.json", ".out.cipher-receipt.json"]
)
def test_operator_metadata_never_enters_source_agent(tmp_path, name):
    (tmp_path / name).write_bytes(b"private")
    files = AgentFiles(Workspace(tmp_path), Dictionary({"alpha": "omega"}))
    with pytest.raises(ValueError, match="Private"):
        files.read(name)


def test_cli_and_mcp_share_recipes_and_keep_agent_tools_separate(tmp_path):
    _, original = setup_files(tmp_path)

    def cli(*args):
        process = subprocess.run(
            [sys.executable, "-m", "bamboozle", "--root", str(tmp_path), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        return json.loads(process.stdout)

    assert cli("cipher-catalog") == capabilities()
    arguments = (
        "cipher-transform",
        "payload.bin",
        "--recipe",
        "transfer.cipher-recipe.json",
        "-o",
        "encoded.bin",
    )
    preview = cli(*arguments)
    assert cli(*arguments, "--apply", "--review-sha256", preview["review_sha256"])[
        "metadata_saved"
    ]
    assert cli("cipher-verify", "encoded.bin")["status"] == "verified"

    async def cycle():
        async with Client(create_server(tmp_path)) as client:

            async def call(name, **args):
                result = await client.call_tool(name, args)
                assert not result.is_error
                return result.data

            assert await call("cipher_catalog") == capabilities()
            selected = await call(
                "preview_cipher_transform",
                source="encoded.bin",
                recipe="transfer.cipher-recipe.json",
                output="restored.bin",
                reverse=True,
            )
            await call("apply_preview", plan_id=selected["plan_id"])
            assert (tmp_path / "restored.bin").read_bytes() == original
            assert (await call("verify_cipher_transform", source="restored.bin"))[
                "status"
            ] == "verified"
        async with Client(agent_server(tmp_path)) as client:
            assert {tool.name for tool in await client.list_tools()} == {
                "list_safe_files",
                "search_safe_files",
                "edit_safe_file",
                "read_safe_file",
                "write_safe_file",
            }

    asyncio.run(cycle())


def test_tampered_output_and_path_aliases_are_refused(tmp_path):
    workspace, _ = setup_files(tmp_path)
    for target in (
        "payload.bin",
        "outside.key",
        "config.cipher-recipe.json",
        "../escaped.bin",
    ):
        with pytest.raises(ValueError):
            prepare_transform(
                workspace, "payload.bin", "transfer.cipher-recipe.json", target
            )
    plan = prepare_transform(
        workspace, "payload.bin", "transfer.cipher-recipe.json", "encoded.bin"
    )
    with pytest.raises(ValueError, match="Review digest"):
        plan.apply("0" * 64)
    plan.apply(plan.review_sha256)
    (tmp_path / "encoded.bin").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="differs"):
        verify_transform(workspace, "encoded.bin")
