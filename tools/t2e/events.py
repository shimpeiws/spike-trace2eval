"""Normalized-event (events.jsonl) inspection.

Parses the coding-worker event log and computes — never assumes — the
checks the PoC needs: consecutive sequence numbers, tool.call/tool.result
pairing by call_id, the recorded initial prompt, check outcomes, identity
events, and explicit truncation markers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .canonical import bounded


@dataclass
class Event:
    seq: int
    type: str
    actor: str
    data: dict[str, Any]
    tool: dict[str, Any] | None


@dataclass
class EventReport:
    event_count: int = 0
    first_seq: int | None = None
    last_seq: int | None = None
    sequence_consecutive: bool = False
    sequence_gaps: list[int] = field(default_factory=list)
    tool_operations: int = 0
    tool_operations_by_name: dict[str, int] = field(default_factory=dict)
    tool_results: int = 0
    unpaired_call_ids: list[str] = field(default_factory=list)
    orphan_result_ids: list[str] = field(default_factory=list)
    duplicate_result_ids: list[str] = field(default_factory=list)
    result_statuses: dict[str, int] = field(default_factory=dict)
    truncation_markers: list[dict[str, Any]] = field(default_factory=list)
    initial_prompt_seq: int | None = None
    initial_prompt_text: str | None = None
    check_events: list[dict[str, Any]] = field(default_factory=list)
    run_started: dict[str, Any] | None = None
    checkout_ready: dict[str, Any] | None = None
    run_finished: dict[str, Any] | None = None
    parse_errors: list[str] = field(default_factory=list)


def _iter_events(path: Path) -> Iterable[tuple[int, Event | str]]:
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                yield lineno, f"line {lineno}: invalid JSON: {bounded(str(e), 200)}"
                continue
            if not isinstance(raw, dict) or not isinstance(raw.get("seq"), int) or not isinstance(raw.get("type"), str):
                yield lineno, f"line {lineno}: event missing seq/type"
                continue
            yield lineno, Event(
                seq=raw["seq"],
                type=raw["type"],
                actor=str(raw.get("actor", "")),
                data=raw.get("data") if isinstance(raw.get("data"), dict) else {},
                tool=raw.get("tool") if isinstance(raw.get("tool"), dict) else None,
            )


def _find_truncations(value: Any, path: str, out: list[str], limit: int = 20) -> None:
    if len(out) >= limit:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "truncated" and v is True:
                out.append(path or "$")
            _find_truncations(v, f"{path}.{k}" if path else k, out, limit)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _find_truncations(v, f"{path}[{i}]", out, limit)


def analyze_events(path: Path) -> EventReport:
    rep = EventReport()
    call_ids: dict[str, str] = {}
    result_ids: dict[str, int] = {}
    prev_seq = 0
    for _lineno, item in _iter_events(path):
        if isinstance(item, str):
            rep.parse_errors.append(item)
            continue
        ev = item
        rep.event_count += 1
        if rep.first_seq is None:
            rep.first_seq = ev.seq
        if ev.seq != prev_seq + 1 and rep.first_seq != ev.seq:
            rep.sequence_gaps.append(ev.seq)
        prev_seq = ev.seq
        rep.last_seq = ev.seq
        if ev.type == "tool.call" and ev.tool:
            cid = str(ev.tool.get("call_id", ""))
            if cid and cid not in call_ids:
                call_ids[cid] = str(ev.tool.get("name", ""))
        elif ev.type == "tool.result" and ev.tool:
            cid = str(ev.tool.get("call_id", ""))
            if cid:
                result_ids[cid] = result_ids.get(cid, 0) + 1
            status = str(ev.data.get("status", ""))
            rep.result_statuses[status] = rep.result_statuses.get(status, 0) + 1
        elif ev.type == "message" and rep.initial_prompt_seq is None:
            rep.initial_prompt_seq = ev.seq
            text = ev.data.get("text")
            rep.initial_prompt_text = text if isinstance(text, str) else None
        elif ev.type == "run.started":
            rep.run_started = ev.data
        elif ev.type == "checkout.ready":
            rep.checkout_ready = ev.data
        elif ev.type == "run.finished":
            rep.run_finished = ev.data
        elif ev.type in ("check.started", "check.finished"):
            entry = {"seq": ev.seq, "kind": ev.type}
            entry.update({k: ev.data.get(k) for k in ("name", "command", "exit_code", "status")})
            rep.check_events.append(entry)
        trunc_paths: list[str] = []
        _find_truncations(ev.data, "", trunc_paths)
        for tp in trunc_paths:
            rep.truncation_markers.append({"seq": ev.seq, "type": ev.type, "field": tp})
    rep.sequence_consecutive = (
        not rep.parse_errors
        and not rep.sequence_gaps
        and rep.first_seq == 1
        and rep.event_count > 0
        and rep.last_seq == rep.event_count
    )
    rep.tool_operations = len(call_ids)
    for name in call_ids.values():
        rep.tool_operations_by_name[name] = rep.tool_operations_by_name.get(name, 0) + 1
    rep.tool_results = len(result_ids)
    rep.unpaired_call_ids = sorted(c for c in call_ids if c not in result_ids)[:50]
    rep.orphan_result_ids = sorted(c for c in result_ids if c not in call_ids)[:50]
    rep.duplicate_result_ids = sorted(c for c, n in result_ids.items() if n > 1)[:50]
    return rep
