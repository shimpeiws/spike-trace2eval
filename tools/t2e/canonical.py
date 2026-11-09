"""Deterministic JSON/hashing helpers.

`canonical_json` mirrors coding-worker's `canonicalStringify`
(src/util/json.ts): JSON.stringify with recursively sorted object keys —
compact separators, non-ASCII emitted literally (UTF-8). Verified against
the retained R1 manifest: sha256(canonical(job.snapshot)) ==
manifest.job_sha256 and likewise for issue_snapshot_sha256.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _sort_deep(value: Any) -> Any:
    if isinstance(value, list):
        return [_sort_deep(v) for v in value]
    if isinstance(value, dict):
        return {k: _sort_deep(value[k]) for k in sorted(value)}
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_sort_deep(value), separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_json_atomic(path: Path, value: Any) -> None:
    write_text_atomic(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def bounded(text: str, limit: int = 2000) -> str:
    """Bound a diagnostic string so a hostile/huge payload cannot flood reports."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[truncated {len(text) - limit} chars]"
