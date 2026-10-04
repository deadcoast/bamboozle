import json

import pytest

from bamboozle import service
from bamboozle.dictionary import Dictionary
from bamboozle.utils import atomic_write
from bamboozle.workspace import Workspace


@pytest.fixture
def sample(tmp_path):
    raw = b"\xef\xbb\xbfAlpha and beta.\r\nALPHA\r\n"
    (tmp_path / "source.txt").write_bytes(raw)
    return Workspace(tmp_path), Dictionary({"alpha": "beta"}), raw


def test_preview_encode_verify_and_restore_preserve_exact_bytes(sample):
    workspace, mapping, original = sample
    plan = service.prepare(workspace, "source.txt", dictionary=mapping)
    preview = service.preview(plan)
    assert preview["status"] == "preview" and "beta" in preview["diff"]
    assert not (workspace.root / "source_safe.txt").exists()
    saved = plan.apply()
    assert saved["output_saved"] and saved["ledger_saved"]
    assert service.verify(workspace, "source_safe.txt")["status"] == "verified"
    restored = service.prepare(workspace, "source_safe.txt", mode="reverse").apply()
    assert (workspace.root / restored["output"]).read_bytes() == original
    assert (workspace.root / "source.txt").read_bytes() == original
    assert service.ledger_path(workspace.root / "source_safe.txt").exists()


def test_changed_source_cannot_apply_preview(sample):
    workspace, mapping, _ = sample
    plan = service.prepare(workspace, "source.txt", dictionary=mapping)
    (workspace.root / "source.txt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed after preview"):
        plan.apply()
    assert not plan.output.exists()


def test_changed_decode_ledger_cannot_apply_preview(sample):
    workspace, mapping, _ = sample
    service.prepare(workspace, "source.txt", dictionary=mapping).apply()
    plan = service.prepare(workspace, "source_safe.txt", mode="reverse")
    path = service.ledger_path(plan.source)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Ledger changed"):
        plan.apply()
    assert not plan.output.exists()


def test_strict_restore_rejects_edited_encoded_file(sample):
    workspace, mapping, _ = sample
    service.prepare(workspace, "source.txt", dictionary=mapping).apply()
    (workspace.root / "source_safe.txt").write_text("edited", encoding="utf-8")
    with pytest.raises(ValueError, match="Encoded file changed"):
        service.prepare(workspace, "source_safe.txt", mode="reverse", strict=True)


def test_ledger_dictionary_tampering_is_rejected(sample):
    workspace, mapping, _ = sample
    service.prepare(workspace, "source.txt", dictionary=mapping).apply()
    path = service.ledger_path(workspace.root / "source_safe.txt")
    ledger = json.loads(path.read_bytes())
    ledger["dictionary"]["entries"]["alpha"] = "gamma"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="dictionary digest"):
        service.verify(workspace, "source_safe.txt")


def test_partial_publication_is_reported_and_recoverable(sample, monkeypatch):
    workspace, mapping, original = sample
    plan = service.prepare(workspace, "source.txt", dictionary=mapping)
    ledger = service.ledger_path(plan.output)

    def fail_ledger(path, data):
        if path == ledger:
            raise PermissionError("ledger blocked")
        atomic_write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(service, "atomic_write", fail_ledger)
        with pytest.raises(service.OperationError) as failure:
            plan.apply()
    assert failure.value.report["output_saved"] is True
    assert failure.value.report["ledger_saved"] is False
    assert plan.output.exists() and not ledger.exists()
    assert (workspace.root / "source.txt").read_bytes() == original
    report = service.recover(workspace, plan.output, "source.txt", mapping)
    assert report["status"] == "recovery_preview" and not ledger.exists()
    service.recover(workspace, plan.output, "source.txt", mapping, apply=True)
    assert service.verify(workspace, plan.output)["status"] == "verified"


def test_racing_destination_is_preserved(sample, monkeypatch):
    workspace, mapping, _ = sample
    plan = service.prepare(workspace, "source.txt", dictionary=mapping)

    def race(path, data):
        path.write_bytes(b"concurrent writer")
        atomic_write(path, data)

    monkeypatch.setattr(service, "atomic_write", race)
    with pytest.raises(service.OperationError) as failure:
        plan.apply()
    assert not failure.value.report["output_saved"]
    assert plan.output.read_bytes() == b"concurrent writer"


def test_translation_expansion_limit_prevents_publication(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("a a a", encoding="utf-8")
    with pytest.raises(ValueError, match="size limit"):
        service.prepare(
            Workspace(tmp_path, max_bytes=100),
            source,
            dictionary=Dictionary({"a": "b" * 50}),
        )
    assert sorted(path.name for path in tmp_path.iterdir()) == ["source.txt"]


def test_in_place_and_existing_destination_refused(sample):
    workspace, mapping, _ = sample
    with pytest.raises(ValueError, match="separate destination"):
        service.prepare(
            workspace, "source.txt", output="source.txt", dictionary=mapping
        )
    (workspace.root / "occupied.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        service.prepare(
            workspace, "source.txt", output="occupied.txt", dictionary=mapping
        )


@pytest.mark.parametrize("operation", ["encode", "recover"])
def test_oversized_ledger_is_rejected_before_publication(tmp_path, operation):
    (tmp_path / "original.txt").write_bytes(b"alpha")
    workspace = Workspace(tmp_path, max_bytes=100)
    mapping = Dictionary({"alpha": "beta"})
    if operation == "recover":
        (tmp_path / "encoded.txt").write_bytes(b"beta")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(ValueError, match="ledger exceeds"):
        if operation == "encode":
            service.prepare(workspace, "original.txt", dictionary=mapping)
        else:
            service.recover(
                workspace, "encoded.txt", "original.txt", mapping, apply=True
            )
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
