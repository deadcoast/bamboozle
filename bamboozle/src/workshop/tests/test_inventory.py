import json
import sys
from pathlib import Path

import pytest

from bamboozle.utils import atomic_write, json_bytes
from workshop import examples, inventory, plans
from workshop import operations as ops
from workshop.bootstrap import initialize
from workshop.mcp import workshop_mcp
from workshop.scrape import bamb5, promote


@pytest.fixture
def native(tmp_path):
    root = tmp_path / "native"
    initialize(root)
    for name in ("ExampleA", "ExampleB"):
        examples.candidate(root, name, ["red hair"])
    return root


def files(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


def prepare(root, name="ExampleA"):
    return plans.prepare_promotion(root, f"candidates/{name}.json")


def apply(root, report):
    return plans.apply_plan(
        root, report["operation_id"], report["plan_sha256"], dry_run=False
    )


def test_empty_old_workshop_listing_is_read_only(native):
    (native / plans.DIRECTORY).rmdir()
    before = files(native)
    result = inventory.list_plans(native)
    assert result["items"] == [] and result["next_cursor"] is None
    assert not result["has_more"]
    assert result["metadata_bytes_read"] == len(before["_vigiles_workshop.json"])
    assert files(native) == before
    assert not (native / plans.DIRECTORY).exists()
    assert not (native / "scrape/.vigiles.lock").exists()


def test_paging_discovers_prepared_stale_cancelled_and_applied(native):
    applied = prepare(native)
    apply(native, applied)
    stale = prepare(native, "ExampleB")
    path = native / "candidates/ExampleB.json"
    path.write_bytes(path.read_bytes() + b" ")
    cancelled = plans.prepare_quarantine(native, "ExampleA", "cancel fixture")
    plans.cancel_plan(native, cancelled["operation_id"], cancelled["plan_sha256"])
    prepared = plans.prepare_quarantine(native, "ExampleA", "keep fixture")
    before = files(native)
    rows = []
    cursor = None
    has_more = True
    for _ in range(4):
        page = inventory.list_plans(native, limit=1, cursor=cursor)
        assert len(page["items"]) == 1
        assert (
            page["asset_hashes_checked"] is False and page["apply_preflight"] is False
        )
        rows.extend(page["items"])
        cursor = page["next_cursor"]
        has_more = page["has_more"]
    assert cursor is None and not has_more
    assert [row["file"] for row in rows] == sorted(row["file"] for row in rows)
    assert {row["operation_id"]: row["status"] for row in rows} == {
        applied["operation_id"]: "applied",
        stale["operation_id"]: "stale",
        cancelled["operation_id"]: "cancelled",
        prepared["operation_id"]: "prepared",
    }
    assert files(native) == before


def test_pending_recovery_and_corrupt_completion_are_visible(native, monkeypatch):
    report = prepare(native)

    def fail(path, content, **kwargs):
        if path == native / ops.LEDGER:
            raise OSError("inventory fixture failure")
        atomic_write(path, content, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ops, "atomic_write", fail)
        with pytest.raises(ops.PendingOperation):
            apply(native, report)
    before = files(native)
    assert inventory.list_plans(native)["items"][0]["status"] == "recovery_required"
    assert files(native) == before
    ops.recover(native, report["operation_id"], dry_run=False)
    done = native / f"scrape/transactions/{report['operation_id']}.done.json"
    done.write_bytes(b"{}")
    row = inventory.list_plans(native)["items"][0]
    assert row["status"] == "corrupt" and "Completion record" in row["error"]


@pytest.mark.parametrize("contents", [b"{", b"[]", b'{"kind": true}', b'{"x":1,"x":2}'])
def test_damaged_json_does_not_hide_other_plans(native, contents):
    report = prepare(native)
    damaged = native / plans.DIRECTORY / ("0" * 32 + ".json")
    damaged.write_bytes(contents)
    before = files(native)
    page = inventory.list_plans(native)
    assert page["items"][0]["status"] == "corrupt"
    assert page["items"][1]["operation_id"] == report["operation_id"]
    assert page["items"][1]["status"] == "prepared"
    assert files(native) == before


def test_orphan_markers_unknown_names_and_nonfiles_are_reported(native):
    directory = native / plans.DIRECTORY
    (directory / ("a" * 32 + ".cancelled.json")).write_bytes(b"{}")
    (directory / "unexpected.tmp").write_bytes(b"retained partial file")
    (directory / ("b" * 32 + ".json")).mkdir()
    page = inventory.list_plans(native)
    assert len(page["items"]) == 3
    assert all(row["status"] == "corrupt" for row in page["items"])


def test_large_asset_hashing_is_deferred_to_inspect_and_apply(native, monkeypatch):
    report = prepare(native)
    artifact = native / report["asset"]["source"]
    artifact.write_bytes(b"changed asset bytes")

    def forbidden(*args, **kwargs):
        raise AssertionError("Inventory must not hash model assets")

    with monkeypatch.context() as patch:
        patch.setattr(ops, "_digest", forbidden)
        page = inventory.list_plans(native)
    assert page["items"][0]["status"] == "prepared"
    assert (
        page["freshness"] == "metadata_only" and page["asset_hashes_checked"] is False
    )
    assert plans.inspect_plan(native, report["operation_id"])["applicable"] is False
    with pytest.raises(ValueError, match="asset changed"):
        apply(native, report)


def test_destination_appeared_is_stale(native):
    report = prepare(native)
    (native / report["asset"]["destination"]).write_bytes(b"other")
    assert inventory.list_plans(native)["items"][0]["status"] == "stale"


def test_cursor_binds_workshop_and_membership(native, tmp_path):
    prepare(native)
    prepare(native)
    page = inventory.list_plans(native, limit=1)
    cursor = page["next_cursor"]
    assert cursor
    other = tmp_path / "other"
    initialize(other)
    with pytest.raises(ValueError, match="restart listing"):
        inventory.list_plans(other, cursor=cursor)
    prepare(native, "ExampleB")
    before = files(native)
    with pytest.raises(ValueError, match="restart listing"):
        inventory.list_plans(native, cursor=cursor)
    assert files(native) == before


@pytest.mark.parametrize("cursor", ["", "../../etc", "a" * 4097, "bnVsbA==", "e30="])
def test_invalid_cursor_is_refused_without_writes(native, cursor):
    before = files(native)
    with pytest.raises(ValueError, match="cursor"):
        inventory.list_plans(native, cursor=cursor)
    assert files(native) == before


@pytest.mark.parametrize("limit", [0, -1, 11, True, 1.5])
def test_page_limits_are_strict(native, limit):
    with pytest.raises(ValueError, match="limit"):
        inventory.list_plans(native, limit=limit)


def test_read_budget_pages_before_next_record_and_discloses_oversized_record(
    native, monkeypatch
):
    prepare(native)
    prepare(native)
    first = inventory.list_plans(native, limit=1)
    budget = first["metadata_bytes_read"] + 1
    monkeypatch.setattr(inventory, "MAX_READ_BYTES", budget)
    result = inventory.list_plans(native, limit=10)
    assert len(result["items"]) == 1 and result["has_more"]
    assert result["metadata_bytes_read"] <= budget
    second = inventory.list_plans(native, cursor=result["next_cursor"])
    assert len(second["items"]) == 1 and not second["has_more"]
    monkeypatch.setattr(
        inventory, "MAX_READ_BYTES", (native / "_vigiles_workshop.json").stat().st_size
    )
    result = inventory.list_plans(native, limit=1)
    assert result["items"][0]["status"] == "unavailable"
    assert "budget" in result["items"][0]["error"]


def test_native_marker_shares_page_read_budget(native, monkeypatch):
    marker = native / "_vigiles_workshop.json"
    marker.write_bytes(marker.read_bytes() + b" " * 4096)
    size = marker.stat().st_size
    monkeypatch.setattr(inventory, "MAX_READ_BYTES", size)
    assert inventory.list_plans(native)["metadata_bytes_read"] == size
    monkeypatch.setattr(inventory, "MAX_READ_BYTES", size - 1)
    with pytest.raises(ValueError, match="budget"):
        inventory.list_plans(native)


def test_long_unicode_names_respect_response_bound_and_page_completely(native):
    directory = native / plans.DIRECTORY
    for index in range(10):
        (directory / (str(index) + "\u754c" * 70 + ".json")).write_bytes(b"{}")
    rows = []
    cursor = None
    while True:
        page = inventory.list_plans(native, limit=10, cursor=cursor)
        assert (
            len(json.dumps(page, indent=2).encode("ascii")) + 1
            <= inventory.MAX_PAGE_BYTES
        )
        rows.extend(row["file"] for row in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(rows) == len(set(rows)) == 10


def test_catalog_limit_fails_explicitly_without_partial_results(native, monkeypatch):
    for index in range(3):
        (native / plans.DIRECTORY / str(index)).write_bytes(b"fixture")
    monkeypatch.setattr(inventory, "MAX_DIRECTORY_ENTRIES", 2)
    before = files(native)
    with pytest.raises(ValueError, match="scan limit"):
        inventory.list_plans(native)
    assert files(native) == before


def test_catalog_change_during_page_requires_restart(native, monkeypatch):
    prepare(native)
    original = inventory._entry

    def change(ws, name):
        result = original(ws, name)
        (native / plans.DIRECTORY / "new.tmp").write_bytes(b"concurrent fixture")
        return result

    monkeypatch.setattr(inventory, "_entry", change)
    with pytest.raises(ValueError, match="changed during listing"):
        inventory.list_plans(native)


def test_changed_metadata_is_distinguished_from_corruption(native, monkeypatch):
    prepare(native)
    original = inventory._Reader.read

    def change(ws, name):
        raw = original(ws, name)
        if ws.resolve(name) == native / ops.MANIFEST:
            (native / ops.MANIFEST).write_bytes(raw + b" ")
        return raw

    monkeypatch.setattr(inventory._Reader, "read", change)
    assert inventory.list_plans(native)["items"][0]["status"] == "changing"


def test_corrupt_cancellation_is_visible(native):
    report = prepare(native)
    path = native / plans.DIRECTORY / f"{report['operation_id']}.cancelled.json"
    path.write_bytes(json_bytes({"status": "cancelled", "plan_sha256": "wrong"}))
    page = inventory.list_plans(native)
    assert len(page["items"]) == 1 and page["items"][0]["status"] == "corrupt"


def test_applied_plan_cannot_hide_conflicting_cancellation(native):
    report = prepare(native)
    apply(native, report)
    marker = native / plans.DIRECTORY / f"{report['operation_id']}.cancelled.json"
    marker.write_bytes(
        json_bytes(
            {
                "status": "cancelled",
                "operation_id": report["operation_id"],
                "plan_sha256": report["plan_sha256"],
            }
        )
    )
    before = files(native)
    page = inventory.list_plans(native)
    assert len(page["items"]) == 1 and page["items"][0]["status"] == "corrupt"
    assert "Conflicting cancellation" in page["items"][0]["error"]
    assert files(native) == before


@pytest.mark.parametrize(
    "asset", [{}, {"source": "x", "destination": "y", "sha256": "invalid"}]
)
def test_incomplete_asset_contract_is_corrupt(native, asset):
    report = prepare(native)
    path = native / report["plan_file"]
    data = json.loads(path.read_bytes())
    data["asset"] = asset
    path.write_bytes(json_bytes(data))
    assert inventory.list_plans(native)["items"][0]["status"] == "corrupt"


@pytest.mark.parametrize("module", [workshop_mcp, bamb5])
def test_both_mcp_adapters_use_real_bounded_inventory_cli(native, monkeypatch, module):
    for _ in range(3):
        prepare(native)
    monkeypatch.setattr(module, "SCRAPE", native / "scrape")
    monkeypatch.setattr(module, "PY", Path(sys.executable))
    before = files(native)
    first = module.list_plans(limit=1)
    assert first["returncode"] == 0 and not first["stdout_truncated"]
    page = json.loads(first["stdout"])
    second = module.list_plans(limit=10, cursor=page["next_cursor"])
    assert second["returncode"] == 0 and not second["stdout_truncated"]
    assert len(json.loads(second["stdout"])["items"]) == 2
    assert files(native) == before


@pytest.mark.parametrize(
    "arguments",
    [
        ["--status", "--limit", "1"],
        ["--status", "--cursor", "x"],
        ["--list-plans", "--dry-run"],
    ],
)
def test_cli_rejects_mixed_inventory_options(native, arguments):
    with pytest.raises(SystemExit) as error:
        promote.main(arguments, root=native)
    assert error.value.code == 2
