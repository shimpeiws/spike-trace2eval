"""R1 evidence validation and initial-input extraction (issue #1).

Reads — never modifies, never executes — a retained coding-worker
evidence directory, recomputes every consistency check for the requested
run, and extracts the deterministic case inputs from the saved job only.

Refusal semantics: any failed check yields a nonzero-exit diagnostic and
no accepted case; the stage record is written either way so later stages
see present-and-valid or present-and-invalid, never ambiguity.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .canonical import (
    bounded,
    canonical_json,
    load_json,
    sha256_bytes,
    sha256_file,
    sha256_text,
)
from .events import EventReport, analyze_events
from .prompt_v2 import build_prompt_v2

EXPECTED_RUN_ID = "20261008T033410Z-7f2a94ec"
EXPECTED_BASE = "99cd09ab71b0ba2807f1d39d0a26dfe042e89bac"
EXPECTED_REPO_CANONICAL = "github.com/shimpeiws/yuurei"
EXPECTED_EVENTS = 6588
EXPECTED_TOOL_OPERATIONS = 85
EXPECTED_CHECKS = 5
# README.md and SHA256SUMS are the only files the evidence format leaves
# out of the checksum list (see the evidence dir's own README).
CHECKSUM_EXEMPT = {"README.md", "SHA256SUMS"}
RUN_REQUIRED_FILES = [
    "manifest.json",
    "job.snapshot.json",
    "result.json",
    "devflow-submission.json",
    "events.jsonl",
    "patch.diff.gz",
]

MAX_DIAGNOSTICS = 40


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class EvidenceResult:
    ok: bool
    checks: list[Check] = field(default_factory=list)
    run_dir: Path | None = None
    job: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    event_report: EventReport | None = None
    prompt_match: bool = False
    prompt_bytes: int = 0
    sha256sums_count: int = 0
    diagnostics: list[str] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(Check(name, ok, detail))
        if not ok:
            self.ok = False
            if len(self.diagnostics) < MAX_DIAGNOSTICS:
                self.diagnostics.append(f"{name}: {bounded(detail, 400)}")


def _parse_sha256sums(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    entries: list[tuple[str, str]] = []
    errors: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = re.match(r"^([0-9a-f]{64}) [ *]?(.*)$", line)
        if not m:
            errors.append(f"line {i}: malformed entry: {bounded(line, 120)}")
            continue
        entries.append((m.group(1), m.group(2).strip()))
    return entries, errors


DEFAULT_EXPECTED = {
    "run_id": EXPECTED_RUN_ID,
    "base_revision": EXPECTED_BASE,
    "repo_canonical": EXPECTED_REPO_CANONICAL,
    "events": EXPECTED_EVENTS,
    "tool_operations": EXPECTED_TOOL_OPERATIONS,
    "checks": EXPECTED_CHECKS,
    "sha256sums_entries": 33,
}


def validate_evidence(
    evidence_dir: Path,
    run_id: str,
    task_record: Path | None = None,
    readiness_record: Path | None = None,
    expected: dict[str, Any] | None = None,
) -> EvidenceResult:
    """Recompute every documented consistency check for one run.

    Processes exactly the run named by `run_id`. Evidence is only read.
    `expected` overrides the R1 case constants for synthetic fixtures.
    """
    exp = {**DEFAULT_EXPECTED, **(expected or {})}
    res = EvidenceResult(ok=True)
    evidence_dir = evidence_dir.resolve()
    if not evidence_dir.is_dir():
        res.add("evidence-dir", False, f"not a directory: {evidence_dir}")
        return res
    res.add("run-selection", run_id == exp["run_id"], f"requested run {run_id}; this PoC implements only run {exp['run_id']}")
    if run_id != exp["run_id"]:
        return res

    # --- SHA256SUMS coverage and contents --------------------------------
    sums_path = evidence_dir / "SHA256SUMS"
    if not sums_path.is_file():
        res.add("sha256sums-present", False, "SHA256SUMS absent")
        return res
    entries, parse_errors = _parse_sha256sums(sums_path.read_text(encoding="utf-8"))
    res.add("sha256sums-parse", not parse_errors, "; ".join(parse_errors[:5]))
    res.sha256sums_count = len(entries)
    res.add("sha256sums-count", len(entries) == exp["sha256sums_entries"], f"{len(entries)} entries (expected {exp['sha256sums_entries']})")

    missing: list[str] = []
    mismatched: list[str] = []
    listed: set[str] = set()
    for digest, rel in entries:
        listed.add(rel)
        target = evidence_dir / rel
        if not target.is_file():
            missing.append(rel)
            continue
        if sha256_file(target) != digest:
            mismatched.append(rel)
    res.add("sha256sums-files-present", not missing, f"missing: {missing[:10]}")
    res.add("sha256sums-match", not mismatched, f"mismatch: {mismatched[:10]}")

    uncovered = sorted(
        str(p.relative_to(evidence_dir))
        for p in evidence_dir.rglob("*")
        if p.is_file() and str(p.relative_to(evidence_dir)) not in listed | CHECKSUM_EXEMPT
    )
    res.add("sha256sums-coverage", not uncovered, f"unlisted files: {uncovered[:10]}")

    # --- run directory + required payloads -------------------------------
    run_dirs = sorted(evidence_dir.glob(f"runs/*/{run_id}"))
    res.add("run-dir", len(run_dirs) == 1, f"{len(run_dirs)} matching run directories")
    if len(run_dirs) != 1:
        return res
    run_dir = run_dirs[0]
    res.run_dir = run_dir
    absent = [f for f in RUN_REQUIRED_FILES if not (run_dir / f).is_file()]
    res.add("run-required-files", not absent, f"absent: {absent}")
    if absent:
        return res

    try:
        job = load_json(run_dir / "job.snapshot.json")
        manifest = load_json(run_dir / "manifest.json")
        result = load_json(run_dir / "result.json")
    except (OSError, ValueError) as e:
        res.add("json-parse", False, bounded(str(e), 300))
        return res
    res.job, res.manifest, res.result = job, manifest, result

    # --- identity: job ↔ manifest ↔ events -------------------------------
    res.add("identity-run-id", manifest.get("run_id") == run_id, f"manifest.run_id={manifest.get('run_id')}")
    res.add("identity-job-id", manifest.get("job_id") == job.get("job_id"), f"job={job.get('job_id')} manifest={manifest.get('job_id')}")
    res.add("identity-base", job.get("base_revision") == manifest.get("base_revision") == exp["base_revision"],
            f"job={job.get('base_revision')} manifest={manifest.get('base_revision')}")
    res.add("identity-repo", (job.get("repository") or {}).get("canonical") == exp["repo_canonical"],
            str((job.get("repository") or {}).get("canonical")))
    job_hash = sha256_text(canonical_json(job))
    res.add("identity-job-hash", job_hash == manifest.get("job_sha256"), f"computed {job_hash[:16]}… vs {manifest.get('job_sha256', '')[:16]}…")
    issue_hash = sha256_text(canonical_json(job.get("issue")))
    res.add("identity-issue-hash", issue_hash == manifest.get("issue_snapshot_sha256"),
            f"computed {issue_hash[:16]}… vs {manifest.get('issue_snapshot_sha256', '')[:16]}…")

    # --- events -----------------------------------------------------------
    events_path = run_dir / "events.jsonl"
    rep = analyze_events(events_path)
    res.event_report = rep
    res.add("events-parse", not rep.parse_errors, "; ".join(rep.parse_errors[:5]))
    res.add("events-sequence", rep.sequence_consecutive,
            f"count={rep.event_count} first={rep.first_seq} last={rep.last_seq} gaps={rep.sequence_gaps[:10]}")
    res.add("events-count", rep.event_count == exp["events"], f"{rep.event_count} events (expected {exp['events']})")
    res.add("tools-pairs", rep.tool_operations == exp["tool_operations"] and rep.tool_results == exp["tool_operations"],
            f"calls={rep.tool_operations} results={rep.tool_results} (expected {exp['tool_operations']})")
    res.add("tools-pairing", not rep.unpaired_call_ids and not rep.orphan_result_ids and not rep.duplicate_result_ids,
            f"unpaired={rep.unpaired_call_ids[:5]} orphan={rep.orphan_result_ids[:5]} dup={rep.duplicate_result_ids[:5]}")

    if rep.run_started:
        res.add("start-job-id", rep.run_started.get("job_id") == job.get("job_id"), str(rep.run_started.get("job_id")))
    else:
        res.add("start-job-id", False, "run.started event absent")
    if rep.checkout_ready:
        res.add("start-revision", rep.checkout_ready.get("revision") == exp["base_revision"], str(rep.checkout_ready.get("revision")))
    else:
        res.add("start-revision", False, "checkout.ready event absent")

    check_names = sorted(e["name"] for e in rep.check_events if e.get("kind") == "check.started" and e.get("name"))
    job_check_names = sorted(c["name"] for c in job.get("checks", []))
    res.add("checks-declared", check_names == job_check_names and len(check_names) == exp["checks"],
            f"events={check_names} job={job_check_names}")
    finished_ok = all(
        e.get("status") == "passed" and e.get("exit_code") == 0
        for e in rep.check_events if e.get("kind") == "check.finished"
    )
    res.add("checks-finished", finished_ok and sum(1 for e in rep.check_events if e.get("kind") == "check.finished") == exp["checks"],
            f"finished={sum(1 for e in rep.check_events if e.get('kind') == 'check.finished')}")

    if rep.run_finished:
        status_ok = rep.run_finished.get("status") == manifest.get("status") == result.get("status")
        res.add("finish-status", status_ok,
                f"event={rep.run_finished.get('status')} manifest={manifest.get('status')} result={result.get('status')}")
    else:
        res.add("finish-status", False, "run.finished event absent")

    # --- prompt -----------------------------------------------------------
    if rep.initial_prompt_text is None:
        res.add("prompt-present", False, "no message event with text")
    else:
        regenerated = build_prompt_v2(job)
        res.prompt_match = regenerated == rep.initial_prompt_text
        res.prompt_bytes = len(rep.initial_prompt_text.encode("utf-8"))
        detail = "byte-equal" if res.prompt_match else (
            f"recorded {res.prompt_bytes}B vs regenerated {len(regenerated.encode('utf-8'))}B; "
            f"first diff at {next((i for i, (a, b) in enumerate(zip(regenerated, rep.initial_prompt_text)) if a != b), min(len(regenerated), len(rep.initial_prompt_text)))}"
        )
        res.add("prompt-v2-match", res.prompt_match, detail)

    # --- manifest artifact hashes ----------------------------------------
    for art in manifest.get("artifacts", []):
        target = run_dir / art.get("path", "")
        ok = target.is_file() and sha256_file(target) == art.get("sha256") and target.stat().st_size == art.get("bytes")
        res.add(f"artifact-{art.get('path')}", ok, f"{target.name} sha/bytes {'match' if ok else 'differ'}")

    # --- optional supporting records --------------------------------------
    if task_record is not None:
        _check_task_record(res, task_record, job)
    if readiness_record is not None:
        _check_readiness(res, readiness_record, task_record)

    # Truncation markers are reported, not failed (payload completeness is a
    # separate property the caller inspects via the record).
    res.add("truncation-reported", True,
            f"{len(rep.truncation_markers)} marker(s): {rep.truncation_markers[:5]}")
    return res


def _check_task_record(res: EvidenceResult, task_record: Path, job: dict[str, Any]) -> None:
    if not task_record.is_file():
        res.add("task-record", False, f"absent: {task_record}")
        return
    try:
        task = load_json(task_record)
    except (OSError, ValueError) as e:
        res.add("task-record", False, bounded(str(e), 300))
        return
    res.add("task-issue-hash", sha256_text(canonical_json(task.get("issue"))) == sha256_text(canonical_json(job.get("issue"))),
            "prepared task and job issue snapshots differ")
    res.add("task-base", task.get("base_revision") == job.get("base_revision"), str(task.get("base_revision")))
    res.add("task-repo", (task.get("repository") or {}).get("canonical") == (job.get("repository") or {}).get("canonical"),
            str((task.get("repository") or {}).get("canonical")))
    res.add("task-checks", canonical_json(task.get("checks")) == canonical_json(job.get("checks")), "check sets differ")


def _check_readiness(res: EvidenceResult, readiness: Path, task_record: Path | None) -> None:
    if not readiness.is_file():
        res.add("readiness-record", False, f"absent: {readiness}")
        return
    text = readiness.read_text(encoding="utf-8")
    res.add("readiness-present", True, f"{len(text)} bytes")
    if task_record is not None and task_record.is_file():
        file_hash = sha256_file(task_record)
        res.add("readiness-task-hash", file_hash in text, f"task.json sha256 {file_hash[:16]}… not recorded in readiness")


def extract_case_fields(job: dict[str, Any]) -> dict[str, Any]:
    """Initial task inputs, every field citing its saved-job source.

    Only saved initial job fields are extracted; observed/inferred/missing
    stay distinguishable, and callers cannot inject unsupported fields.
    """
    issue = job["issue"]
    constraints = job.get("constraints") or {}

    def prov(source: str, classification: str = "observed") -> dict[str, str]:
        return {"source": source, "classification": classification}

    fields: dict[str, Any] = {
        "title": {"value": issue["title"], **prov("job.issue.title")},
        "body": {"value": issue["body"], **prov("job.issue.body")},
        "issue_url": {"value": issue.get("url"), **prov("job.issue.url")},
        "issue_number": {"value": issue.get("number"), **prov("job.issue.number")},
        "acceptance_criteria": {"value": issue.get("acceptance_criteria", []), **prov("job.issue.acceptance_criteria")},
        "constraints": {"value": constraints, **prov("job.constraints")},
        "checks": {"value": job.get("checks", []), **prov("job.checks")},
        "budgets": {
            "value": {
                "timeout_seconds": job.get("timeout_seconds"),
                "max_steps": (job.get("limits") or {}).get("max_steps"),
                "max_command_seconds": (job.get("limits") or {}).get("max_command_seconds"),
            },
            **prov("job.timeout_seconds,job.limits"),
        },
        "repository": {"value": job.get("repository"), **prov("job.repository")},
        "base_revision": {"value": job.get("base_revision"), **prov("job.base_revision")},
        "runtime": {"value": job.get("runtime"), **prov("job.runtime")},
    }
    return fields


def case_identity(fields: dict[str, Any]) -> dict[str, Any]:
    """Path- and time-independent identity core for the generated case."""
    return {
        "repository_canonical": (fields["repository"]["value"] or {}).get("canonical"),
        "base_revision": fields["base_revision"]["value"],
        "title": fields["title"]["value"],
        "body": fields["body"]["value"],
        "acceptance_criteria": fields["acceptance_criteria"]["value"],
        "constraints": fields["constraints"]["value"],
        "checks": fields["checks"]["value"],
        "budgets": fields["budgets"]["value"],
    }


def case_id_for(identity_core: dict[str, Any]) -> str:
    return "r1-" + sha256_text(canonical_json(identity_core))[:16]
