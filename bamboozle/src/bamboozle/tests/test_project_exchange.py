"""Project transport failures and real source/receiver/return application workflows."""

import copy
import runpy
import struct
from pathlib import Path

import pytest

from bamboozle import project_exchange as exchange
from bamboozle import view_import
from bamboozle.dictionary import Dictionary
from bamboozle.project_views import CONTRACT, export_view, snapshot_view
from bamboozle.service import OperationError
from bamboozle.tessera import seal
from bamboozle.utils import atomic_write, json_bytes, sha256
from bamboozle.workspace import Workspace


@pytest.fixture(params=[False, True], ids=["tessera", "classical-tessera"])
def cycle(tmp_path, request):
    owner, peer = tmp_path / "owner", tmp_path / "peer"
    owner.mkdir()
    peer.mkdir()
    for root in (owner, peer):
        (root / "transport.key").write_bytes(bytes(range(32)))
        (root / "chain.cipher-recipe.json").write_bytes(
            json_bytes(
                {
                    "schema_version": 1,
                    "steps": [
                        {
                            "cipher": "polybius-square",
                            "variant": "framed5-v1",
                            "params": {},
                        },
                        {
                            "cipher": "columnar-transposition",
                            "variant": "ragged-v1",
                            "params": {"key": "BALLOON"},
                        },
                    ],
                }
            )
        )
    (owner / "main.py").write_bytes(b"def task(x):\r\n    return x + 1\r\n")
    (owner / "old.py").write_bytes(b"OLD = True\n")
    (owner / "policy.json").write_bytes(
        json_bytes(
            {
                "schema_version": 2,
                "files": {
                    "main.py": {
                        "policy": "translate",
                        "document": {
                            "format": "python",
                            "identifiers": ["task"],
                            "modules": ["main"],
                        },
                    },
                    "old.py": {"policy": "copy"},
                },
                "new_files": {
                    ".": {
                        "policy": "translate",
                        "document": {
                            "format": "python",
                            "identifiers": ["task"],
                            "modules": ["main"],
                        },
                    }
                },
            }
        )
    )
    workspace, remote = Workspace(owner), Workspace(peer)
    view = export_view(workspace, "policy.json", "view", Dictionary({"task": "stage"}))
    options = {
        "key": "transport.key",
        "recipient": "test",
        "recipe": "chain.cipher-recipe.json" if request.param else None,
    }
    packed = exchange.pack_view(workspace, view["view_id"], "out.bam", **options)
    (peer / "out.bam").write_bytes((owner / "out.bam").read_bytes())
    return workspace, remote, view["view_id"], packed["exchange_sha256"], options


def returned(cycle):
    owner, peer, _view_id, exchange_id, options = cycle
    exchange.open_exchange(peer, "out.bam", "work", **options)
    (peer.root / "work/main.py").write_bytes(b"def stage(x):\r\n    return x * 2\r\n")
    (peer.root / "work/new.py").write_bytes(
        b"from main import stage\nassert stage(7) == 14\n"
    )
    exchange.pack_return(peer, exchange_id, "return.bam", **options)
    (owner.root / "return.bam").write_bytes((peer.root / "return.bam").read_bytes())
    return {"return_packet": "return.bam", **options}


def test_complete_return_preserves_local_view_and_exact_review(cycle):
    owner, peer, view_id, exchange_id, _options = cycle
    before = snapshot_view(Workspace(owner.root / "view"))
    args = returned(cycle)
    review = view_import.review_import(owner, view_id, **args)
    assert review["ok"] and review["action_count"] == 2
    assert review["exchange_sha256"] == sha256((owner.root / "return.bam").read_bytes())
    applied = view_import.apply_import(owner, view_id, review["review_sha256"], **args)
    assert applied["receipt_saved"]
    assert (
        owner.root / "main.py"
    ).read_bytes() == b"def task(x):\r\n    return x * 2\r\n"
    assert b"import task" in (owner.root / "new.py").read_bytes()
    assert snapshot_view(Workspace(owner.root / "view")) == before
    status = view_import.view_status(owner, view_id)
    assert status["status"] == "complete" and status["view_changed"] is None
    with pytest.raises(ValueError, match="completed"):
        view_import.apply_import(owner, view_id, review["review_sha256"], **args)
    assert exchange.exchange_context(peer, exchange_id)["mcp_config"] == {
        "mcpServers": {}
    }


def test_authentication_recipe_origin_and_source_drift(cycle):
    owner, peer, view_id, _, options = cycle
    with pytest.raises(ValueError, match="authentication"):
        (peer.root / "wrong.key").write_bytes(bytes(32))
        exchange.open_exchange(
            peer, "out.bam", "bad", **{**options, "key": "wrong.key"}
        )
    assert not (peer.root / "bad").exists()
    with pytest.raises(ValueError, match="recipient"):
        exchange.open_exchange(
            peer, "out.bam", "bad", **{**options, "recipient": "wrong"}
        )
    wrong_recipe = None if options["recipe"] else "chain.cipher-recipe.json"
    with pytest.raises(ValueError, match="recipe"):
        exchange.open_exchange(
            peer, "out.bam", "bad", **{**options, "recipe": wrong_recipe}
        )
    args = returned(cycle)
    other = export_view(owner, "policy.json", "other", Dictionary({"task": "stage"}))
    with pytest.raises(ValueError, match="different view"):
        view_import.review_import(owner, other["view_id"], **args)
    reviewed = view_import.review_import(owner, view_id, **args)
    (owner.root / "main.py").write_bytes(b"CONCURRENT = True\n")
    assert not view_import.review_import(owner, view_id, **args)["ok"]
    with pytest.raises(OperationError, match="changed"):
        view_import.apply_import(owner, view_id, reviewed["review_sha256"], **args)


def test_changed_packet_cannot_apply_old_review(cycle):
    owner, peer, view_id, exchange_id, options = cycle
    args = returned(cycle)
    reviewed = view_import.review_import(owner, view_id, **args)
    (peer.root / "work/main.py").write_bytes(b"def stage(x):\n    return x * 3\n")
    exchange.pack_return(peer, exchange_id, "second.bam", **options)
    (owner.root / "return.bam").write_bytes((peer.root / "second.bam").read_bytes())
    with pytest.raises(OperationError, match="stale"):
        view_import.apply_import(owner, view_id, reviewed["review_sha256"], **args)


def test_explicit_deletion_and_private_return_refusal(cycle):
    owner, peer, view_id, exchange_id, options = cycle
    args = returned(cycle)
    (peer.root / "work/old.py").unlink()
    exchange.pack_return(peer, exchange_id, "deleted.bam", **options)
    (owner.root / "return.bam").write_bytes((peer.root / "deleted.bam").read_bytes())
    assert not view_import.review_import(owner, view_id, **args)["ok"]
    review = view_import.review_import(owner, view_id, delete_files=["old.py"], **args)
    assert review["ok"]
    view_import.apply_import(
        owner, view_id, review["review_sha256"], delete_files=["old.py"], **args
    )
    assert not (owner.root / "old.py").exists()
    (peer.root / "work/leak.key").write_bytes(b"secret")
    with pytest.raises(ValueError, match="Private"):
        exchange.pack_return(peer, exchange_id, "leak.bam", **options)
    assert not (peer.root / "leak.bam").exists()


def test_open_recovery_retains_partial_files_and_refuses_conflicts(cycle, monkeypatch):
    _, peer, _, exchange_id, options = cycle

    def fail(path, raw, **kwargs):
        if path.name == "main.py":
            raise OSError("injected extraction failure")
        atomic_write(path, raw, **kwargs)

    monkeypatch.setattr(exchange, "atomic_write", fail)
    with pytest.raises(OperationError) as caught:
        exchange.open_exchange(peer, "out.bam", "work", **options)
    assert (
        caught.value.report["journal_saved"]
        and not caught.value.report["receipt_saved"]
    )
    assert (peer.root / "work" / CONTRACT).exists()
    with pytest.raises(FileNotFoundError):
        exchange.exchange_context(peer, exchange_id)
    monkeypatch.setattr(exchange, "atomic_write", atomic_write)
    with pytest.raises(FileExistsError):
        exchange.open_exchange(peer, "out.bam", "work", **options)
    (peer.root / "work/main.py").write_bytes(b"conflict")
    with pytest.raises(OperationError, match="changed or unexpected"):
        exchange.open_exchange(peer, "out.bam", "work", recover=True, **options)
    (peer.root / "work/main.py").unlink()
    assert (
        exchange.open_exchange(peer, "out.bam", "work", recover=True, **options)[
            "status"
        ]
        == "recovered"
    )


def test_import_recovery_uses_retained_authenticated_bytes_without_packet_or_key(
    cycle, monkeypatch
):
    owner, _, view_id, _, _ = cycle
    args = returned(cycle)
    reviewed = view_import.review_import(owner, view_id, **args)

    def fail(path, raw, **kwargs):
        if path == owner.root / "new.py":
            raise OSError("injected source publication failure")
        atomic_write(path, raw, **kwargs)

    monkeypatch.setattr(view_import, "atomic_write", fail)
    with pytest.raises(OperationError) as caught:
        view_import.apply_import(owner, view_id, reviewed["review_sha256"], **args)
    assert caught.value.report["journal_saved"] and caught.value.report["output_saved"]
    assert view_import.view_status(owner, view_id)["status"] == "pending"
    for name in ("return.bam", "transport.key", "chain.cipher-recipe.json"):
        (owner.root / name).unlink()
    monkeypatch.setattr(view_import, "atomic_write", atomic_write)
    result = view_import.apply_import(
        owner, view_id, reviewed["review_sha256"], recover=True
    )
    assert result["status"] == "recovered" and result["receipt_saved"]
    assert b"import task" in (owner.root / "new.py").read_bytes()


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "C:/escape",
        "a\\b",
        "AUX.txt",
        ".local/leak",
        ".git/config",
        "leak.key",
        "x.dictionary.json",
        ".env.production",
        "private.pem",
    ],
)
def test_malicious_authenticated_paths_rejected_before_publication(tmp_path, name):
    workspace = Workspace(tmp_path)
    key = bytes(32)
    (tmp_path / "transport.key").write_bytes(key)
    header = {
        "kind": "bamboozle.project-exchange",
        "schema_version": 1,
        "direction": "outbound",
        "binding": {
            "view_id": "a" * 32,
            "manifest_sha256": "b" * 64,
            "controls": {CONTRACT: sha256(b"rules")},
        },
        "files": [{"path": name, "bytes": 0, "sha256": sha256(b"")}],
    }
    encoded = json_bytes(header)
    packet = seal(
        exchange.MAGIC + bytes(32) + struct.pack(">I", len(encoded)) + encoded,
        key,
        "test",
    )
    (tmp_path / "bad.bam").write_bytes(packet)
    with pytest.raises(ValueError):
        exchange.open_exchange(
            workspace, "bad.bam", "work", key="transport.key", recipient="test"
        )
    assert not (tmp_path / "work").exists()
    assert not (tmp_path / exchange.STORAGE).exists()


def test_frame_integrity_duplicates_bounds_and_trailing_bytes(cycle):
    _, peer, _, _, options = cycle
    header, snapshot, _ = exchange.read_exchange(peer, "out.bam", **options)
    raw = exchange._frame(header["binding"], "outbound", snapshot)
    for invalid in (raw[:-1], raw + b"extra", raw[:3], b"\xff" * 4 + raw[4:]):
        with pytest.raises(ValueError):
            exchange._unframe(invalid)
    for mutate in ("duplicate", "collision", "oversize", "bool", "contract"):
        changed = copy.deepcopy(header)
        if mutate == "duplicate":
            changed["files"].append(changed["files"][0])
        elif mutate == "collision":
            changed["files"][1]["path"] = CONTRACT.lower()
        elif mutate == "oversize":
            changed["files"][1]["bytes"] = 16 * 1024 * 1024 + 1
        elif mutate == "bool":
            changed["files"][1]["bytes"] = True
        else:
            changed["binding"]["controls"][CONTRACT] = "f" * 64
        with pytest.raises(ValueError):
            exchange._header(changed)


def test_packet_tampering_and_limits_publish_nothing(cycle):
    _, peer, _, _, options = cycle
    import json

    packet = json.loads((peer.root / "out.bam").read_bytes())
    data = packet["ciphertext"]
    packet["ciphertext"] = ("A" if data[0] != "A" else "B") + data[1:]
    (peer.root / "bad.bam").write_bytes(json_bytes(packet))
    with pytest.raises(ValueError, match="authentication"):
        exchange.open_exchange(peer, "bad.bam", "work", **options)
    assert not (peer.root / "work").exists()
    with pytest.raises(ValueError, match="limit"):
        exchange.open_exchange(Workspace(peer.root, 32), "out.bam", "work", **options)
    assert not (peer.root / "work").exists()


def test_outbound_state_controls_and_destination_boundaries(cycle):
    owner, peer, view_id, exchange_id, options = cycle
    with pytest.raises(FileExistsError):
        exchange.pack_view(owner, view_id, "out.bam", **options)
    with pytest.raises(ValueError, match="outside"):
        exchange.pack_view(owner, view_id, "view/packet.bam", **options)
    (owner.root / "view/main.py").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        exchange.pack_view(owner, view_id, "changed.bam", **options)
    exchange.open_exchange(peer, "out.bam", "work", **options)
    with pytest.raises(ValueError, match="already opened"):
        exchange.open_exchange(peer, "out.bam", "second", **options)
    (peer.root / "work" / CONTRACT).write_bytes(b"changed")
    with pytest.raises(ValueError, match="controls changed"):
        exchange.pack_return(peer, exchange_id, "changed.bam", **options)
    assert not (peer.root / "changed.bam").exists()


def test_vandal_receiver_context_uses_product_without_extra_agent_tools(
    cycle, monkeypatch
):
    _, peer, _, exchange_id, options = cycle
    opened = exchange.open_exchange(peer, "out.bam", "work", **options)
    repository = Path(__file__).parents[3]
    function = runpy.run_path(str(repository / "scripts/vandal.py"))["context"]
    for name in (
        "pyproject.toml",
        ".mivim/vigiles-vandal_0.2.md",
        ".vigiles/skills/vigiles-vandal/SKILL.md",
    ):
        destination = peer.root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((repository / name).read_bytes())
    monkeypatch.setitem(function.__globals__, "ROOT", peer.root)
    context = function("exchange", exchange_id=exchange_id)
    assert context["workspace"] == opened["agent_context"]["workspace"]
    assert context["mcp_config"] == {"mcpServers": {}}
    assert context["cipher_capabilities"]["project_exchange"]["operator_only"]
    with pytest.raises(ValueError, match="Choose"):
        function("agent", exchange_id=exchange_id)


def test_complete_cli_and_stdio_applications(tmp_path):
    run = runpy.run_path(str(Path(__file__).parents[3] / "scripts/exchange_smoke.py"))[
        "run"
    ]
    result = run(tmp_path / "applications")
    assert set(result["workflows"]) == {"cli", "mcp"}
    for flow in result["workflows"].values():
        assert flow["restored_benchmark"] == flow["neutral_benchmark"]
        assert flow["restored_benchmark"]["scheduler_executions"] == 300
        assert flow["local_view_unchanged"] and flow["apply"]["receipt_saved"]
