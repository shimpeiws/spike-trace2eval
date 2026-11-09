"""Stage records: absent / present-and-valid / present-and-invalid.

Every pipeline stage emits a JSON record. A stage's readiness is only
established by a present record that parses, matches its stage schema, and
carries status "passed" — never by an absent or invalid record, and never
by inference from the 2026-11-09 read-only investigation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .canonical import bounded, load_json, write_json_atomic

SCHEMA_VERSION = 1
RECORD_STATUSES = ("passed", "failed")


class RecordState(Enum):
    ABSENT = "absent"
    INVALID = "invalid"
    VALID = "valid"


@dataclass
class LoadedRecord:
    state: RecordState
    record: dict[str, Any] | None = None
    diagnostics: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.state is RecordState.VALID


def record_path(records_dir: Path, stage: str) -> Path:
    return records_dir / f"{stage}.json"


def write_record(records_dir: Path, stage: str, status: str, **fields: Any) -> Path:
    """Atomically write a stage record. status is 'passed' or 'failed'."""
    if status not in RECORD_STATUSES:
        raise ValueError(f"invalid record status: {status}")
    rec = {"schema_version": SCHEMA_VERSION, "stage": stage, "status": status}
    rec.update(fields)
    path = record_path(records_dir, stage)
    write_json_atomic(path, rec)
    return path


def load_record(path: Path, stage: str) -> LoadedRecord:
    """Classify a stage record as ABSENT / INVALID / VALID.

    VALID requires: parses as a JSON object, schema_version 1, matching
    stage name, status in RECORD_STATUSES, and status == 'passed'. A
    well-formed record with status 'failed' is INVALID (present but does
    not establish readiness). Diagnostics are bounded.
    """
    if not path.exists():
        return LoadedRecord(RecordState.ABSENT, diagnostics=[f"missing record: {path.name}"])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return LoadedRecord(RecordState.INVALID, diagnostics=[f"unparsable record {path.name}: {bounded(str(e), 300)}"])
    if not isinstance(raw, dict):
        return LoadedRecord(RecordState.INVALID, diagnostics=[f"record {path.name} is not a JSON object"])
    problems: list[str] = []
    if raw.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version != {SCHEMA_VERSION}")
    if raw.get("stage") != stage:
        problems.append(f"stage {raw.get('stage')!r} != {stage!r}")
    status = raw.get("status")
    if status not in RECORD_STATUSES:
        problems.append(f"status {status!r} not in {RECORD_STATUSES}")
    elif status != "passed":
        detail = raw.get("diagnostics") or raw.get("failure") or "no detail recorded"
        problems.append(f"status is 'failed': {bounded(str(detail), 300)}")
    if problems:
        return LoadedRecord(RecordState.INVALID, record=raw, diagnostics=problems)
    return LoadedRecord(RecordState.VALID, record=raw)


def require_record(records_dir: Path, stage: str) -> LoadedRecord:
    """Load a stage record; callers treat non-ready as refusal."""
    return load_record(record_path(records_dir, stage), stage)
