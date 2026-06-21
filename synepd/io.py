"""Loaders for SynEPD v0.1.0 JSON and JSONL exports."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List

from synepd.models import Case


def load_cases(path: Path | str) -> List[Case]:
    """Load all cases from the canonical full JSON (or .json.gz)."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    raw_cases = data["cases"] if isinstance(data, dict) else data
    return [Case.from_dict(c) for c in raw_cases]


def load_cases_jsonl(path: Path | str) -> Iterator[Case]:
    """Stream cases one at a time from the JSONL export."""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield Case.from_dict(json.loads(line))


def load_summary(path: Path | str) -> Dict[str, Any]:
    """Load the compact dataset summary JSON."""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
