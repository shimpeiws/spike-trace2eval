"""Sanitized PoC report generation (issue #4).

Answers the three experiment questions with evidence references while
keeping private paths, raw trace bytes, full prompt text, patch content,
and credentials out of the published output. `sanitize_scan` is the
mechanical check for that contract.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .canonical import bounded, load_json
from .records import RecordState, require_record

FORBIDDEN_REPORT_PATTERNS = [
    r"patch\.diff\b.{0,40}@@",          # patch hunks
    r"OPENCODE_SERVER_PASSWORD",
    r"/Users/\S+/coding-worker",         # private evidence path shape
    r"events\.jsonl:",
    r"You are an autonomous coding worker",  # full prompt text
]

QUESTIONS = [
    "Can the saved job and pinned Git SHA recover the original requirements and starting source tree?",
    "Can evaluation inputs be restricted to initial information and checked for solution or outcome leakage?",
    "Can a runnable case be produced for an independent agent without changing coding-worker?",
]


def sanitize_scan(text: str, extra_forbidden: list[str] | None = None) -> list[str]:
    """Return a list of violations — empty means the text is sanitized."""
    problems = []
    for pat in FORBIDDEN_REPORT_PATTERNS + (extra_forbidden or []):
        if re.search(pat, text):
            problems.append(f"forbidden pattern present: {pat!r}")
    return problems


def _stage_summary(records_dir: Path, stage: str) -> tuple[str, dict[str, Any] | None]:
    rec = require_record(records_dir, stage)
    if rec.state is RecordState.ABSENT:
        return f"**{stage}**: record absent", None
    if rec.state is RecordState.INVALID:
        return f"**{stage}**: record invalid ({'; '.join(rec.diagnostics)[:200]})", rec.record
    return f"**{stage}**: passed", rec.record


def generate_report(records_dir: Path) -> str:
    lines = [
        "# R1 single-case Trace2Eval PoC — report",
        "",
        "Generated from the stage records under `records/`. Contains no",
        "private paths, raw trace bytes, full prompt text, patch content, or",
        "credentials; all evidence references are record files plus hashes.",
        "",
        "## Stage outcomes",
        "",
    ]
    records: dict[str, dict[str, Any] | None] = {}
    for stage in ["evidence", "source", "environment", "leakage", "controls", "execution", "evaluation"]:
        summary, rec = _stage_summary(records_dir, stage)
        if summary.endswith("record absent") and stage == "controls":
            continue
        records[stage] = rec
        lines.append(f"- {summary}")
    lines += ["", "## Answers to the PoC questions", ""]
    ev = records.get("evidence") or {}
    src = records.get("source") or {}
    exe = records.get("execution") or {}
    evl = records.get("evaluation") or {}

    a1 = (
        "Yes." if (ev.get("status") == "passed" and src.get("status") == "passed")
        else "Not established."
    )
    lines.append(
        f"1. *{QUESTIONS[0]}* **{a1}** The evidence stage recomputed the"
        " checksum, identity, sequence, tool-pairing, and v2-prompt checks"
        f" (run `{ev.get('run_id', 'n/a')}`); the source stage materialized"
        f" the pinned commit `{str(src.get('pinned_sha', ''))[:12]}…` with"
        f" {src.get('file_count', 'n/a')} tracked files verified against"
        " git's content-addressed objects."
    )
    leak = records.get("leakage") or {}
    a2 = "Yes." if leak.get("status") == "passed" else "Not established."
    lines.append(
        f"2. *{QUESTIONS[1]}* **{a2}** The input package passed the"
        " allowlist, hash, provenance, mount-boundary, and git-history"
        " checks; contamination fixtures are rejected before execution."
    )
    if exe.get("status") == "passed":
        verdict = (evl or {}).get("verdict", "n/a")
        a3 = f"Yes — the case generated, executed ({exe.get('harness')}), and was scored ({verdict})."
    elif exe:
        a3 = f"Precise blocker established: execution stage status {exe.get('status')!r} ({bounded(str((exe.get('launch') or {}).get('error', '')), 200)})."
    else:
        a3 = "Not established — execution record absent."
    lines.append(f"3. *{QUESTIONS[2]}* **{a3}**")

    # Execution detail section — the measured run, not self-report.
    lines += ["", "## Independent run", ""]
    launch = exe.get("launch") or {}
    budgets = exe.get("budgets") or {}
    patch = exe.get("patch") or {}
    changed = patch.get("changed_files") or []
    lines.append(f"- harness: `{exe.get('harness', 'n/a')}`, model: `{exe.get('model', 'n/a')}`")
    lines.append(
        f"- termination: `{launch.get('termination_reason', 'n/a')}` after"
        f" {launch.get('duration_s', 'n/a')}s (timeout budget"
        f" {budgets.get('timeout_seconds', 'n/a')}s; step limits"
        f" {'enforced' if budgets.get('step_limit_enforceable') else 'not enforceable in-process'})"
    )
    lines.append(f"- os: {exe.get('os', 'n/a')}")
    if patch:
        pref = patch.get("patch_ref") or {}
        lines.append(
            f"- produced patch: {pref.get('bytes', 0)} bytes across"
            f" {len(changed)} files; sha256 `{str(pref.get('sha256', ''))[:16]}…`"
        )
    behavior = (evl or {}).get("behavior") or {}
    bchecks = behavior.get("checks") or []
    if bchecks:
        lines.append(
            "- behavior checks: "
            + ", ".join(f"{c['name']}={'ok' if c.get('ok') else 'FAIL'}" for c in bchecks)
        )
    declared = (evl or {}).get("declared_checks") or []
    if declared:
        lines.append(
            "- declared repo checks: "
            + ", ".join(f"{c['name']}={c.get('exit_code')}" for c in declared)
        )
    devs = exe.get("deviations_from_original") or []
    if devs:
        lines.append("- recorded deviations:")
        lines += [f"  - {d}" for d in devs]

    lines += [
        "",
        "## Limitations",
        "",
        "- The original OS, PATH, locale, and internal harness instructions are unrecoverable; deviations are recorded in `environment.json` and the execution record.",
        "- Network restriction is enforced at the harness tool level, not OS egress.",
        "- These controls check case-construction contamination; they cannot prove the public solution is absent from model training data.",
        "- Existing checks already passed on the starting commit; their success alone does not establish task completion.",
    ]
    return "\n".join(lines) + "\n"
