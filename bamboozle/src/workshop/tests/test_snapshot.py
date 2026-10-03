"""Reader behavior during actual journaled publication and recovery."""

import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESSIV
from fastmcp import Client

from bamboozle.utils import atomic_write, json_bytes
from bamboozle.workspace import Workspace
from workshop import examples, snapshot
from workshop import operations as ops
from workshop.bootstrap import initialize
from workshop.mcp import workshop_mcp as server
from workshop.pipeline import inspect_pipeline
from workshop.scrape import alias_pass, bamb5, lookup
from workshop.scrape.audit_matrix import audit


@pytest.fixture
def native(tmp_path, monkeypatch):
    root = tmp_path / "native"
    initialize(root)
    examples.candidate(root, "ExampleA", ["red hair"])
    examples.candidate(root, "ExampleB", ["red hair", "film grain"])
    for module in (server, bamb5):
        for name, relative in {
            "SCRAPE": "scrape",
            "MANIFEST": ops.MANIFEST,
            "MATRIX": ops.MATRIX,
            "LEDGER": ops.LEDGER,
            "TAXONOMY": "scrape/taxonomy.json",
        }.items():
            monkeypatch.setattr(module, name, root / relative)
    monkeypatch.setattr(bamb5, "KEY_PATH", root / "scrape/_bamb5.key")
    monkeypatch.setattr(lookup, "MANIFEST", root / ops.MANIFEST)
    monkeypatch.setattr(lookup, "INDEX", root / "scrape/_lookup_index.json")
    monkeypatch.setattr(alias_pass, "TAXONOMY", root / "scrape/taxonomy.json")
    monkeypatch.setattr(server, "_IDX", None)
    return root


def files(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


def promote(root, name="ExampleA"):
    return ops.promote(root, f"candidates/{name}.json", dry_run=False)


def load_index():
    return lookup.Index.load(lookup.MANIFEST, alias_pass.TAXONOMY, lookup.INDEX)


@pytest.mark.parametrize("action", ["promote", "quarantine", "audit"])
def test_pending_blocks_every_reader_and_preserves_cache_until_recovery(
    native, monkeypatch, action
):
    promote(native)
    server._idx()
    cached = lookup.INDEX.read_bytes()

    def fail_ledger(path, content, **kwargs):
        if path == native / ops.LEDGER:
            raise OSError("injected post-publication failure")
        atomic_write(path, content, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ops, "atomic_write", fail_ledger)
        with pytest.raises(ops.PendingOperation) as failure:
            if action == "promote":
                promote(native, "ExampleB")
            elif action == "quarantine":
                ops.quarantine(native, "ExampleA", "fixture", dry_run=False)
            else:
                audit(native, apply=True)

    before = files(native)
    for module in (server, bamb5):
        readers = [
            module.query_manifest,
            lambda module=module: module.compatibility("ExampleA", "ExampleB"),
            lambda module=module: module.lookup_trigger_conflicts("ExampleA"),
            lambda module=module: module.normalize_triggers(["red hair"]),
            module.ledger_tail,
        ]
        for read in readers:
            with pytest.raises(ValueError, match="pending"):
                read()
    for read in (
        load_index,
        server._idx,
        lambda: lookup.Index.build(lookup.MANIFEST, alias_pass.TAXONOMY),
        lambda: alias_pass.load_prod_triggers(lookup.MANIFEST),
    ):
        with pytest.raises(ValueError, match="pending"):
            read()
    state = server.pipeline_status(rebuild_index=True)
    assert state["status"] == "recovery_required" and not state["readable"]
    assert state["index_rebuild"] == "blocked_pending_recovery"
    assert not inspect_pipeline(native / "scrape", Path(sys.executable))["ok"]
    assert files(native) == before
    assert lookup.INDEX.read_bytes() == cached
    ops.recover(native, failure.value.operation_id, dry_run=False)
    expected = (
        {"ExampleA", "ExampleB"}
        if action == "promote"
        else set()
        if action == "quarantine"
        else {"ExampleA"}
    )
    assert set(server._idx().by_lora) == expected
    assert {entry["name"] for entry in server.query_manifest()} == expected
    assert server.pipeline_status(rebuild_index=True)["index_rebuild"] == "completed"


def test_completed_publication_during_snapshot_is_rejected_without_cache_write(
    native, monkeypatch
):
    promote(native)
    load_index()
    cached = lookup.INDEX.read_bytes()
    original = Workspace.read
    raced = False

    def read(ws, name):
        nonlocal raced
        raw = original(ws, name)
        if Path(name) == native / ops.MANIFEST and not raced:
            raced = True
            promote(native, "ExampleB")
        return raw

    with monkeypatch.context() as patch:
        patch.setattr(Workspace, "read", read)
        with pytest.raises(ValueError, match="changed during snapshot"):
            load_index()
    assert raced and lookup.INDEX.read_bytes() == cached
    assert set(load_index().by_lora) == {"ExampleA", "ExampleB"}


def test_active_writer_blocks_reader_before_control_changes(native, monkeypatch):
    started, release = Event(), Event()
    original = ops._asset_copy

    def paused(*args):
        started.set()
        assert release.wait(10)
        original(*args)

    monkeypatch.setattr(ops, "_asset_copy", paused)
    with ThreadPoolExecutor(max_workers=1) as executor:
        operation = executor.submit(promote, native)
        try:
            assert started.wait(10)
            assert json.loads((native / ops.MANIFEST).read_bytes())["loras"] == {}
            with pytest.raises(ValueError, match="pending"):
                server.query_manifest()
            assert not lookup.INDEX.exists()
        finally:
            release.set()
        assert operation.result(timeout=10)["status"] == "saved"
    assert server.query_manifest()[0]["name"] == "ExampleA"


def test_stack_uses_matrix_from_same_snapshot_even_if_quarantined_afterward(
    native, monkeypatch
):
    promote(native)
    promote(native, "ExampleB")
    matrix = {"pairs": {"ExampleA|ExampleB": {"score": 0.85}}}
    (native / ops.MATRIX).write_bytes(json_bytes(matrix))
    ix = server._idx()
    ops.quarantine(native, "ExampleB", "fixture", dry_run=False)
    with monkeypatch.context() as patch:
        patch.setattr(server, "_idx", lambda: ix)
        result = server.stack_check(["ExampleA", "ExampleB"])
    assert result["pairs"][0]["matrix"] == 0.85
    assert server.stack_check(["ExampleA", "ExampleB"])["missing"] == ["ExampleB"]


def test_index_cipher_and_fingerprint_use_captured_bytes(native, monkeypatch):
    promote(native)
    token = bamb5.encode("red hair")
    original = lookup._inputs

    def rotate_after_capture(*args):
        captured = original(*args)
        bamb5.KEY_PATH.write_bytes(AESSIV.generate_key(512))
        return captured

    with monkeypatch.context() as patch:
        patch.setattr(lookup, "_inputs", rotate_after_capture)
        old = load_index()
    assert old.who(token)["loras"] == ["ExampleA"]
    assert load_index().fp != old.fp


def test_native_index_includes_tags_used_by_audit(native):
    candidate = native / "candidates/ExampleA.json"
    data = json.loads(candidate.read_bytes())
    data["triggers"] = []
    data["tags"] = ["red hair"]
    candidate.write_bytes(json_bytes(data))
    promote(native)
    assert load_index().who("red hair")["loras"] == ["ExampleA"]


def test_corrupt_completion_rejects_cached_queries(native):
    result = promote(native)
    server._idx()
    done = native / f"scrape/transactions/{result['operation_id']}.done.json"
    done.write_bytes(b'{"status":"saved"}')
    before = files(native)
    with pytest.raises(ValueError, match="Completion record"):
        server.lora_who("red hair")
    assert files(native) == before


@pytest.mark.parametrize(
    "status",
    [{"status": "saved"}, {"status": "publication_recorded", "operation_id": "a" * 32}],
)
def test_unbound_status_is_not_a_readable_snapshot(native, status):
    (native / ops.STATUS).write_bytes(json_bytes(status))
    with pytest.raises(ValueError, match="status does not match"):
        server.query_manifest()
    assert not inspect_pipeline(native / "scrape", Path(sys.executable))["ok"]
    assert not server.pipeline_status()["readable"]


def test_fresh_workshop_reads_do_not_create_state(native):
    before = files(native)
    assert server.query_manifest() == []
    assert server.ledger_tail() == []
    assert inspect_pipeline(native / "scrape", Path(sys.executable))["ok"]
    assert files(native) == before


def test_relative_workshop_paths_are_resolved_from_cwd(native, monkeypatch):
    monkeypatch.chdir(native.parent)
    before = files(native)
    assert inspect_pipeline(Path("native/scrape"), Path(sys.executable))["ok"]
    manifest = Path("native/scrape/_lora_manifest.json")
    assert snapshot.load_json(manifest)["loras"] == {}
    assert files(native) == before


def test_native_index_uses_selected_workshop_key(native, monkeypatch):
    promote(native)
    monkeypatch.setattr(bamb5, "KEY_PATH", native.parent / "missing-other-workshop.key")
    assert load_index().who("red hair")["loras"] == ["ExampleA"]


@pytest.mark.parametrize("n", [0, -1, 1001])
def test_ledger_rows_are_bounded(native, n):
    for module in (server, bamb5):
        with pytest.raises(ValueError, match="between"):
            module.ledger_tail(n)


def test_actual_mcp_pending_query_returns_error_and_status_remains_available(
    native, monkeypatch
):
    def fail(path, content, **kwargs):
        if path == native / ops.LEDGER:
            raise OSError("fixture")
        atomic_write(path, content, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ops, "atomic_write", fail)
        with pytest.raises(ops.PendingOperation):
            promote(native)

    async def check():
        async with Client(server.mcp) as client:
            result = await client.call_tool("query_manifest", {}, raise_on_error=False)
            assert result.is_error
            status = await client.call_tool("pipeline_status", {"rebuild_index": True})
            assert not status.is_error
            assert "recovery_required" in str(status.content)

    asyncio.run(check())
    assert not lookup.INDEX.exists()
