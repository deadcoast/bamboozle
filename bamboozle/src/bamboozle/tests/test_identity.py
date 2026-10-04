"""Admission, lifecycle, protocol separation and whole-application regressions."""

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from bamboozle import identity, project_exchange, tessera, transport, view_import
from bamboozle.dictionary import Dictionary
from bamboozle.project_views import export_view
from bamboozle.service import OperationError
from bamboozle.utils import atomic_write, json_bytes, sha256
from bamboozle.workspace import Workspace


@pytest.fixture
def peers(tmp_path):
    (tmp_path / "owner").mkdir()
    (tmp_path / "receiver").mkdir()
    owner, receiver = Workspace(tmp_path / "owner"), Workspace(tmp_path / "receiver")
    for workspace, name, peer in (
        (owner, "owner", "receiver"),
        (receiver, "receiver", "owner"),
    ):
        identity.initialize(workspace, name)
        (workspace.root / "first.key").write_bytes(bytes(range(32)))
        (workspace.root / "second.key").write_bytes(bytes(reversed(range(32))))
        identity.change_peer(workspace, peer, action="register", key="first.key")
    return owner, receiver


def packet(owner, data=b"\x00data\xff", purpose="file", ttl=86400):
    with identity.sending(owner, "receiver", data, purpose, ttl) as raw:
        return raw


def change(workspace, peer, action, **kwargs):
    return identity.change_peer(
        workspace,
        peer,
        action=action,
        expected_sha256=identity.status(workspace)["registry_sha256"],
        **kwargs,
    )


def test_lifecycle_requires_fresh_digests_and_distinct_keys(peers):
    owner, receiver = peers
    before = identity.status(owner)
    with pytest.raises(ValueError, match="changed"):
        identity.change_peer(
            owner,
            "receiver",
            action="rotate",
            key="second.key",
            expected_sha256="0" * 64,
        )
    with pytest.raises(ValueError, match="already bound"):
        identity.change_peer(owner, "outsider", action="register", key="first.key")
    with pytest.raises(ValueError, match="distinct"):
        identity.change_peer(owner, "owner", action="register", key="second.key")
    key_id = before["peers"]["receiver"]["active"]
    change(owner, "receiver", "revoke", key_id=key_id)
    with pytest.raises(ValueError, match="active"):
        packet(owner)
    with pytest.raises(ValueError, match="already bound"):
        change(owner, "receiver", "rotate", key="first.key")
    change(owner, "receiver", "rotate", key="second.key")
    assert identity.status(owner)["peers"]["receiver"]["keys"][key_id] == "revoked"
    with pytest.raises(ValueError, match="Unknown"):
        identity.authenticate(receiver, packet(owner), "owner", "file")
    change(receiver, "owner", "rotate", key="second.key")
    assert (
        identity.authenticate(receiver, packet(owner), "owner", "file")[0]
        == b"\x00data\xff"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("sender", "other"),
        ("recipient", "other"),
        ("purpose", "project-return"),
        ("message_id", "0" * 32),
        ("expires_at", 1),
        ("schema_version", 1),
        ("nonce", "AAAA"),
        ("ciphertext", "A" * 32),
    ],
)
def test_tamper_never_creates_admission(peers, field, value):
    owner, receiver = peers
    raw = json.loads(packet(owner))
    raw[field] = value
    with pytest.raises((ValueError, TypeError)):
        identity.authenticate(receiver, json_bytes(raw), "owner", "file")
    assert not (receiver.root / identity.ROOT / "deliveries").exists()


def test_protocols_do_not_downgrade_or_substitute_purpose(peers):
    owner, receiver = peers
    raw = packet(owner)
    with pytest.raises(ValueError):
        tessera.unseal(raw, bytes(range(32)), "receiver")
    with pytest.raises(ValueError, match="schema-2"):
        identity.authenticate(
            receiver,
            tessera.seal(b"data", bytes(range(32)), "receiver"),
            "owner",
            "file",
        )
    with pytest.raises(ValueError, match="purpose mismatch"):
        identity.authenticate(receiver, raw, "owner", "project-outbound")
    with pytest.raises(ValueError, match="--peer"):
        project_exchange.read_exchange(
            receiver, "missing", key="first.key", recipient="receiver", peer="owner"
        )


def test_expiry_future_clock_and_retirement_cutoff(peers, monkeypatch):
    owner, receiver = peers
    monkeypatch.setattr(identity.time, "time", lambda: 1000)
    raw = packet(owner, ttl=100)
    monkeypatch.setattr(identity.time, "time", lambda: 900)
    with pytest.raises(ValueError, match="future"):
        identity.authenticate(receiver, raw, "owner", "file")
    monkeypatch.setattr(identity.time, "time", lambda: 1050)
    change(receiver, "owner", "rotate", key="second.key")
    assert identity.authenticate(receiver, raw, "owner", "file")[0]
    monkeypatch.setattr(identity.time, "time", lambda: 1060)
    with pytest.raises(ValueError, match="retired"):
        identity.authenticate(receiver, packet(owner), "owner", "file")
    monkeypatch.setattr(identity.time, "time", lambda: 1100)
    with pytest.raises(ValueError, match="expired"):
        identity.authenticate(receiver, raw, "owner", "file")


def test_file_recovery_preserves_reservation_and_rejects_replay(peers, monkeypatch):
    owner, receiver = peers
    (receiver.root / "data.bam").write_bytes(packet(owner))

    def fail(path, data, **kwargs):
        if path.name == "result.json":
            raise OSError("injected receipt interruption")
        atomic_write(path, data, **kwargs)

    monkeypatch.setattr(transport, "atomic_write", fail)
    with pytest.raises(OperationError) as caught:
        transport.transfer_file(
            receiver, "data.bam", "out.bin", peer="owner", opening=True
        )
    identifier = caught.value.report["delivery_id"]
    assert identity.delivery_status(receiver, identifier)["status"] == "pending"
    assert (receiver.root / "out.bin").read_bytes() == b"\x00data\xff"
    monkeypatch.setattr(transport, "atomic_write", atomic_write)
    with pytest.raises(OperationError, match="replay"):
        transport.transfer_file(
            receiver, "data.bam", "other.bin", peer="owner", opening=True
        )
    with pytest.raises(OperationError):
        transport.transfer_file(
            receiver, "data.bam", "other.bin", peer="owner", opening=True, recover=True
        )
    assert not (receiver.root / "other.bin").exists()
    assert transport.transfer_file(
        receiver, "data.bam", "out.bin", peer="owner", opening=True, recover=True
    )["receipt_saved"]
    assert identity.delivery_status(receiver, identifier)["status"] == "completed"
    raw = json.loads((receiver.root / "data.bam").read_bytes())
    (receiver.root / "replay.bam").write_text(json.dumps(raw, separators=(",", ":")))
    with pytest.raises(OperationError, match="replay"):
        transport.transfer_file(
            receiver,
            "replay.bam",
            "other.bin",
            peer="owner",
            opening=True,
            recover=True,
        )


@pytest.mark.parametrize("revoke", [False, True])
def test_pending_delivery_recovers_after_expiry_but_not_revocation(
    peers, monkeypatch, revoke
):
    owner, receiver = peers
    monkeypatch.setattr(identity.time, "time", lambda: 1000)
    (receiver.root / "data.bam").write_bytes(packet(owner, ttl=1))

    def fail(path, data, **kwargs):
        if path.name == "out.bin":
            raise OSError("interrupted")
        atomic_write(path, data, **kwargs)

    monkeypatch.setattr(transport, "atomic_write", fail)
    with pytest.raises(OperationError):
        transport.transfer_file(
            receiver, "data.bam", "out.bin", peer="owner", opening=True
        )
    monkeypatch.setattr(transport, "atomic_write", atomic_write)
    monkeypatch.setattr(identity.time, "time", lambda: 1100)
    if revoke:
        change(receiver, "owner", "revoke", key_id=sha256(bytes(range(32))))
        with pytest.raises(OperationError, match="revoked"):
            transport.transfer_file(
                receiver,
                "data.bam",
                "out.bin",
                peer="owner",
                opening=True,
                recover=True,
            )
        assert not (receiver.root / "out.bin").exists()
    else:
        assert (
            transport.transfer_file(
                receiver,
                "data.bam",
                "out.bin",
                peer="owner",
                opening=True,
                recover=True,
            )["status"]
            == "recovered"
        )


def project(peers):
    owner, receiver = peers
    (owner.root / "main.py").write_bytes(b"def task(x):\n    return x + 1\n")
    (owner.root / "policy.json").write_bytes(
        json_bytes(
            {
                "schema_version": 2,
                "files": {"main.py": {"policy": "translate"}},
                "new_files": {
                    ".": {"policy": "translate", "document": {"format": "text"}}
                },
            }
        )
    )
    view = export_view(owner, "policy.json", "view", Dictionary({"task": "stage"}))
    packed = project_exchange.pack_view(
        owner, view["view_id"], "out.bam", peer="receiver"
    )
    (receiver.root / "out.bam").write_bytes((owner.root / "out.bam").read_bytes())
    return view["view_id"], packed["exchange_sha256"]


@pytest.mark.parametrize("fail_at", ["main.py", "result.json"])
def test_project_extraction_retains_one_delivery_through_recovery(
    peers, monkeypatch, fail_at
):
    _, receiver = peers
    _, exchange_id = project(peers)

    def fail(path, data, **kwargs):
        if path.name == fail_at:
            raise OSError("injected extraction failure")
        atomic_write(path, data, **kwargs)

    monkeypatch.setattr(project_exchange, "atomic_write", fail)
    with pytest.raises(OperationError) as caught:
        project_exchange.open_exchange(receiver, "out.bam", "work", peer="owner")
    identifier = caught.value.report["delivery_id"]
    assert identity.delivery_status(receiver, identifier)["status"] == "pending"
    monkeypatch.setattr(project_exchange, "atomic_write", atomic_write)
    with pytest.raises(ValueError, match="replay"):
        project_exchange.open_exchange(receiver, "out.bam", "other", peer="owner")
    project_exchange.open_exchange(
        receiver, "out.bam", "work", peer="owner", recover=True
    )
    assert identity.delivery_status(receiver, identifier)["status"] == "completed"
    assert project_exchange.exchange_context(receiver, exchange_id)["workspace"] == str(
        receiver.root / "work"
    )
    with pytest.raises(ValueError, match="original registered"):
        project_exchange.pack_return(
            receiver, exchange_id, "wrong.bam", key="first.key", recipient="owner"
        )


@pytest.mark.parametrize("revoke", [False, True])
def test_review_apply_recovery_keeps_authenticated_admission(
    peers, monkeypatch, revoke
):
    owner, receiver = peers
    view_id, exchange_id = project(peers)
    project_exchange.open_exchange(receiver, "out.bam", "work", peer="owner")
    (receiver.root / "work/main.py").write_bytes(b"def stage(x):\n    return x * 2\n")
    (receiver.root / "work/new.py").write_bytes(
        b"from main import stage\nassert stage(7) == 14\n"
    )
    project_exchange.pack_return(receiver, exchange_id, "return.bam", peer="owner")
    (owner.root / "return.bam").write_bytes((receiver.root / "return.bam").read_bytes())
    review = view_import.review_import(
        owner, view_id, return_packet="return.bam", peer="receiver"
    )
    assert review == view_import.review_import(
        owner, view_id, return_packet="return.bam", peer="receiver"
    )
    assert not (owner.root / identity.ROOT / "deliveries").exists()

    def fail(path, data, **kwargs):
        if path == owner.root / "new.py":
            raise OSError("injected source interruption")
        atomic_write(path, data, **kwargs)

    monkeypatch.setattr(view_import, "atomic_write", fail)
    with pytest.raises(OperationError) as caught:
        view_import.apply_import(
            owner,
            view_id,
            review["review_sha256"],
            return_packet="return.bam",
            peer="receiver",
        )
    assert caught.value.report["applied_files"] == ["main.py"]
    assert identity.delivery_status(owner, review["delivery_id"])["status"] == "pending"
    monkeypatch.setattr(view_import, "atomic_write", atomic_write)
    (owner.root / "return.bam").unlink()
    if revoke:
        change(owner, "receiver", "revoke", key_id=sha256(bytes(range(32))))
        with pytest.raises(OperationError, match="revoked"):
            view_import.apply_import(
                owner, view_id, review["review_sha256"], recover=True
            )
        assert not (owner.root / "new.py").exists()
        assert view_import.view_status(owner, view_id)["status"] != "complete"
    else:
        change(owner, "receiver", "rotate", key="second.key")
        view_import.apply_import(owner, view_id, review["review_sha256"], recover=True)
        assert (
            identity.delivery_status(owner, review["delivery_id"])["status"]
            == "completed"
        )
        assert b"import task" in (owner.root / "new.py").read_bytes()
        change(owner, "receiver", "revoke", key_id=sha256(bytes(range(32))))
        assert view_import.view_status(owner, view_id)["status"] == "complete"


def test_complete_managed_cli_and_stdio_applications(tmp_path):
    execute = runpy.run_path(
        str(Path(__file__).parents[3] / "scripts/identity_smoke.py")
    )["run"]
    result = execute(tmp_path / "applications")
    for flow in result["applications"]["workflows"].values():
        assert flow["restored_benchmark"] == flow["neutral_benchmark"]
        assert flow["restored_benchmark"]["scheduler_executions"] == 300
    for flow in result["lifecycle"].values():
        assert flow["rotation_received"] and flow["revocation_refused"]


def test_processes_cannot_admit_one_delivery_twice(peers):
    owner, receiver = peers
    (receiver.root / "data.bam").write_bytes(packet(owner))
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "bamboozle",
                "--root",
                str(receiver.root),
                "open",
                "data.bam",
                "-o",
                name,
                "--peer",
                "owner",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for name in ("first.bin", "second.bin")
    ]
    reports = []
    try:
        for process in processes:
            output, error = process.communicate(timeout=30)
            assert output, error
            reports.append((process.returncode, json.loads(output)))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
    assert sorted(code for code, _ in reports) == [0, 1]
    assert (
        sum((receiver.root / name).exists() for name in ("first.bin", "second.bin"))
        == 1
    )
    assert len(list((receiver.root / identity.ROOT / "deliveries").glob("*.json"))) == 1


def test_custody_lock_excludes_concurrent_key_revocation(peers):
    owner, _ = peers
    registry = identity.status(owner)
    with identity.sending(owner, "receiver", b"data", "file"):
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "bamboozle",
                "--root",
                str(owner.root),
                "recipient-revoke",
                "receiver",
                "--key-id",
                sha256(bytes(range(32))),
                "--expected-sha256",
                registry["registry_sha256"],
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert (
            process.returncode == 1 and "active" in json.loads(process.stdout)["error"]
        )
    assert identity.status(owner)["registry_sha256"] == registry["registry_sha256"]
    change(owner, "receiver", "revoke", key_id=sha256(bytes(range(32))))


def test_journal_before_admission_crash_recovers_without_rewriting_intent(
    peers, monkeypatch
):
    owner, receiver = peers
    (receiver.root / "data.bam").write_bytes(packet(owner))

    def fail(path, data, **kwargs):
        if path.parent.name == "deliveries":
            raise OSError("interrupted before admission")
        atomic_write(path, data, **kwargs)

    monkeypatch.setattr(identity, "atomic_write", fail)
    with pytest.raises(OperationError) as caught:
        transport.transfer_file(
            receiver, "data.bam", "out.bin", peer="owner", opening=True
        )
    assert (
        caught.value.report["journal_saved"]
        and not (receiver.root / "out.bin").exists()
    )
    monkeypatch.setattr(identity, "atomic_write", atomic_write)
    assert (
        transport.transfer_file(
            receiver, "data.bam", "out.bin", peer="owner", opening=True, recover=True
        )["status"]
        == "recovered"
    )


def test_unchanged_return_is_still_consumed_once(peers):
    owner, receiver = peers
    view_id, exchange_id = project(peers)
    project_exchange.open_exchange(receiver, "out.bam", "work", peer="owner")
    project_exchange.pack_return(receiver, exchange_id, "return.bam", peer="owner")
    (owner.root / "return.bam").write_bytes((receiver.root / "return.bam").read_bytes())
    review = view_import.review_import(
        owner, view_id, return_packet="return.bam", peer="receiver"
    )
    assert review["action_count"] == 0
    view_import.apply_import(
        owner,
        view_id,
        review["review_sha256"],
        return_packet="return.bam",
        peer="receiver",
    )
    assert (
        identity.delivery_status(owner, review["delivery_id"])["status"] == "completed"
    )
    with pytest.raises(ValueError, match="replay"):
        project_exchange.read_exchange(
            owner, "return.bam", peer="receiver", purpose="project-return"
        )


def test_post_review_revocation_blocks_source_publication(peers):
    owner, receiver = peers
    view_id, exchange_id = project(peers)
    project_exchange.open_exchange(receiver, "out.bam", "work", peer="owner")
    (receiver.root / "work/main.py").write_bytes(b"def stage(x):\n    return x * 2\n")
    project_exchange.pack_return(receiver, exchange_id, "return.bam", peer="owner")
    (owner.root / "return.bam").write_bytes((receiver.root / "return.bam").read_bytes())
    review = view_import.review_import(
        owner, view_id, return_packet="return.bam", peer="receiver"
    )
    original = (owner.root / "main.py").read_bytes()
    change(owner, "receiver", "revoke", key_id=sha256(bytes(range(32))))
    with pytest.raises(OperationError, match="revoked"):
        view_import.apply_import(
            owner,
            view_id,
            review["review_sha256"],
            return_packet="return.bam",
            peer="receiver",
        )
    assert (owner.root / "main.py").read_bytes() == original


@pytest.mark.parametrize("opening", [False, True])
def test_existing_output_is_not_reported_as_published_by_refusal(peers, opening):
    owner, receiver = peers
    workspace = receiver if opening else owner
    (workspace.root / "input.bin").write_bytes(packet(owner) if opening else b"data")
    (workspace.root / "occupied.bin").write_bytes(b"untouched")
    with pytest.raises(OperationError) as caught:
        transport.transfer_file(
            workspace,
            "input.bin",
            "occupied.bin",
            peer="owner" if opening else "receiver",
            opening=opening,
        )
    assert caught.value.report["output_saved"] is False
    assert caught.value.report["output_exists"] is True
    assert (workspace.root / "occupied.bin").read_bytes() == b"untouched"


@pytest.mark.parametrize("project_packet", [False, True])
def test_key_drift_at_send_completion_preserves_publication_evidence(
    peers, monkeypatch, project_packet
):
    owner, _ = peers
    (owner.root / "input.bin").write_bytes(b"payload")
    key = owner.root / identity.ROOT / "keys" / f"{sha256(bytes(range(32)))}.key"

    def changed_key(path, data, **kwargs):
        atomic_write(path, data, **kwargs)
        if path.name == "changed.bam":
            key.write_bytes(bytes(32))

    if project_packet:
        view_id, _ = project(peers)
        monkeypatch.setattr(project_exchange, "atomic_write", changed_key)
        with pytest.raises(OperationError, match="fingerprint") as caught:
            project_exchange.pack_view(owner, view_id, "changed.bam", peer="receiver")
    else:
        monkeypatch.setattr(transport, "atomic_write", changed_key)
        with pytest.raises(OperationError, match="fingerprint") as caught:
            transport.transfer_file(
                owner, "input.bin", "changed.bam", peer="receiver", opening=False
            )
    assert caught.value.report["output_saved"] is True
    assert (owner.root / "changed.bam").exists()


def test_custody_close_failure_retains_completed_extraction_report(peers, monkeypatch):
    _, receiver = peers
    _, exchange_id = project(peers)
    key = receiver.root / identity.ROOT / "keys" / f"{sha256(bytes(range(32)))}.key"

    def changed_key(path, data, **kwargs):
        atomic_write(path, data, **kwargs)
        if path.name == "result.json":
            key.write_bytes(bytes(32))

    monkeypatch.setattr(project_exchange, "atomic_write", changed_key)
    with pytest.raises(OperationError, match="fingerprint") as caught:
        project_exchange.open_exchange(receiver, "out.bam", "work", peer="owner")
    assert caught.value.report["receipt_saved"] and caught.value.report["output_saved"]
    assert (
        identity.delivery_status(receiver, caught.value.report["delivery_id"])["status"]
        == "completed"
    )
    assert project_exchange.exchange_context(receiver, exchange_id)["workspace"] == str(
        receiver.root / "work"
    )
