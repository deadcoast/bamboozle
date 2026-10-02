import json
import os
import subprocess

import pytest

from bamboozle.audit import audit, baseline
from bamboozle.workspace import Workspace


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        ".git/config",
        ".venv/private",
        "stream:secret",
        ".git/../.git/config",
    ],
)
def test_workspace_rejects_escapes_and_protected_paths(tmp_path, name):
    with pytest.raises(ValueError):
        Workspace(tmp_path).resolve(name)


def test_workspace_preserves_specification_and_limits_reads(tmp_path):
    (tmp_path / "large.txt").write_bytes(b"12345")
    workspace = Workspace(tmp_path, max_bytes=4)
    with pytest.raises(ValueError, match="limit"):
        workspace.read("large.txt")
    with pytest.raises(ValueError, match="specification"):
        workspace.resolve(".mivim/mivim.vigiles.md", write=True)


def test_real_directory_link_cannot_escape_workspace(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt":
            pytest.skip(f"OS does not grant symlink creation: {exc}")
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(root / "link"), str(outside)],
            check=True,
            capture_output=True,
            text=True,
        )
    with pytest.raises(ValueError, match="Symlinks"):
        Workspace(root).resolve("link/file.txt", write=True)


def test_baseline_and_audit_report_drift_without_writes(tmp_path):
    workspace = Workspace(tmp_path)
    (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
    (tmp_path / "removed.md").write_text("old", encoding="utf-8")
    assert audit(workspace)["status"] == "unbaselined"
    assert not (tmp_path / ".vigiles").exists()
    baseline(workspace)
    assert audit(workspace)["ok"]
    previous = (tmp_path / ".vigiles/baseline.json").read_bytes()
    (tmp_path / "main.py").write_text("print(2)", encoding="utf-8")
    (tmp_path / "new.json").write_text("{}", encoding="utf-8")
    (tmp_path / "removed.md").unlink()
    result = audit(workspace)
    assert result["changed"] == ["main.py"]
    assert result["added"] == ["new.json"]
    assert result["removed"] == ["removed.md"]
    assert not result["ok"]
    assert (tmp_path / ".vigiles/baseline.json").read_bytes() == previous
    with pytest.raises(FileExistsError):
        baseline(workspace)


def test_audit_excludes_key_material_and_validates_baseline(tmp_path):
    (tmp_path / "private.key").write_bytes(b"secret")
    workspace = Workspace(tmp_path)
    baseline(workspace)
    path = tmp_path / ".vigiles/baseline.json"
    document = json.loads(path.read_bytes())
    assert "private.key" not in document["files"]
    document["files"]["../outside.py"] = "0" * 64
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="inside the workspace"):
        audit(workspace)
