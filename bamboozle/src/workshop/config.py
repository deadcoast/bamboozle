"""Portable workshop locations; importing this module never creates state."""

from __future__ import annotations

import os
import sys
from pathlib import Path

WORKSHOP = (
    Path(os.environ.get("VIGILES_WORKSHOP_ROOT", str(Path.cwd() / ".vigiles/workshop")))
    .expanduser()
    .resolve()
)
SCRAPE = WORKSHOP / "scrape"
PYTHON = (
    Path(os.environ.get("VIGILES_WORKSHOP_PYTHON", sys.executable))
    .expanduser()
    .resolve()
)
MANIFEST = SCRAPE / "_lora_manifest.json"
MATRIX = SCRAPE / "_compatibility_matrix.json"
LEDGER = SCRAPE / "_lora_ledger.csv"
STATUS = SCRAPE / "_pipeline_status.json"
KEY = SCRAPE / "_bamb5.key"
INDEX = SCRAPE / "_lookup_index.json"
TAXONOMY = SCRAPE / "taxonomy.json"
if not TAXONOMY.exists():
    TAXONOMY = Path(__file__).parent / "scrape/taxonomy.json"
