"""Create a native workshop without replacing existing project state."""

from __future__ import annotations

import csv
import io
from importlib.resources import files
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESSIV

from bamboozle.utils import atomic_write, json_bytes
from workshop.operations import FIELDS, LEDGER, MANIFEST, MATRIX, STATUS


def initialize(root: Path) -> dict[str, Any]:
    root = root.absolute()
    if root.is_symlink() or root.is_junction():
        raise ValueError("Workshop root cannot be a link")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise FileExistsError(
            "Workshop initialization requires a new or empty directory"
        )
    root.mkdir(parents=True, exist_ok=True)
    for directory in (
        "scrape/transactions",
        "scrape/prepared",
        "candidates",
        "prod",
        "quarantine",
    ):
        (root / directory).mkdir(parents=True)
    atomic_write(
        root / "_vigiles_workshop.json",
        json_bytes({"kind": "vigiles.workshop", "schema_version": 1}),
    )
    atomic_write(root / MANIFEST, json_bytes({"schema_version": 1, "loras": {}}))
    atomic_write(root / MATRIX, json_bytes({"schema_version": 1, "pairs": {}}))
    ledger = io.StringIO(newline="")
    csv.writer(ledger).writerow(FIELDS)
    atomic_write(root / LEDGER, ledger.getvalue().encode("utf-8"))
    atomic_write(root / STATUS, json_bytes({"status": "initialized"}))
    atomic_write(root / "scrape/_bamb5.key", AESSIV.generate_key(512))
    atomic_write(
        root / "scrape/taxonomy.json",
        files("workshop.scrape").joinpath("taxonomy.json").read_bytes(),
    )
    for module in ("promote", "audit_matrix"):
        wrapper = (
            f"from pathlib import Path\nfrom workshop.scrape.{module} import main\n\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main(root=Path(__file__).resolve().parents[1]))\n"
        )
        atomic_write(root / "scrape" / f"{module}.py", wrapper.encode("utf-8"))
    return {
        "status": "initialized",
        "workshop": str(root.resolve()),
        "source": "installed workshop.scrape modules",
        "model_qualified": False,
    }
