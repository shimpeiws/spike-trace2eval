"""Synthetic, non-secret evidence fixtures for the t2e test suite.

Builds a miniature retained-evidence directory with the same structure as
the real R1 evidence (SHA256SUMS, runs/<batch>/<run_id>/, manifest,
job.snapshot, result, events.jsonl, commands/) but entirely fabricated
content — no real trace, prompt, patch, or credential material.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from t2e.canonical import canonical_json, sha256_text  # noqa: E402
from t2e.prompt_v2 import build_prompt_v2  # noqa: E402

FIXTURE_RUN = "20990101T000000Z-fixtur01"
FIXTURE_BASE = "a" * 40
FIXTURE_REPO = "github.com/example/fixture-repo"
FIXTURE_JOB_ID = "fixture-job-1"

EXPECTED = {
    "run_id": FIXTURE_RUN,
    "base_revision": FIXTURE_BASE,
    "repo_canonical": FIXTURE_REPO,
    "events": 0,  # computed at build time
    "tool_operations": 2,
    "checks": 2,
    "sha256sums_entries": 0,  # computed at build time
}


def make_job() -> dict:
    return {
        "schema_version": 1,
        "job_id": FIXTURE_JOB_ID,
        "repository": {"location": "https://example.invalid/repo.git", "canonical": FIXTURE_REPO},
        "base_revision": FIXTURE_BASE,
        "issue": {
            "title": "Fixture issue title",
            "body": "Fixture body text.\n",
            "url": "https://example.invalid/issues/1",
            "number": 1,
            "acceptance_criteria": ["Fixture criterion one."],
        },
        "constraints": {
            "allowed_paths": ["src/"],
            "forbidden_paths": ["**/node_modules/**"],
            "notes": "Fixture notes.",
        },
        "credentials_ref": "env:FIXTURE_CREDENTIAL",
        "timeout_seconds": 100,
        "limits": {"max_steps": 10, "max_command_seconds": 50},
        "checks": [
            {"name": "check-one", "command": "echo one", "required": True, "timeout_seconds": 50},
            {"name": "check-two", "command": "echo two", "required": True, "timeout_seconds": 50},
        ],
        "runtime": {"adapter": "dsh", "model": {"provider": "p", "id": "m"}, "prompt_version": "v2"},
    }


def make_events(job: dict, prompt: str) -> list[dict]:
    events = [
        {"seq": 1, "ts": "2099-01-01T00:00:00.000Z", "actor": "core", "type": "run.started",
         "data": {"adapter": "dsh", "job_id": job["job_id"]}},
        {"seq": 2, "ts": "2099-01-01T00:00:01.000Z", "actor": "core", "type": "checkout.ready",
         "data": {"revision": job["base_revision"]}},
        {"seq": 3, "ts": "2099-01-01T00:00:02.000Z", "actor": "adapter:dsh", "type": "message",
         "data": {"role": "assistant", "text": prompt}},
    ]
    seq = 4
    for i, (tool, cid) in enumerate([("bash", "call-1"), ("read", "call-2")]):
        events.append({"seq": seq, "ts": "2099-01-01T00:00:03.000Z", "actor": "adapter:dsh",
                       "type": "tool.call", "tool": {"name": tool, "call_id": cid},
                       "data": {"input": {"command": f"cmd-{i}"}}})
        seq += 1
        events.append({"seq": seq, "ts": "2099-01-01T00:00:04.000Z", "actor": "adapter:dsh",
                       "type": "tool.result", "tool": {"name": tool, "call_id": cid},
                       "data": {"status": "completed", "title": f"cmd-{i}", "input": {"command": f"cmd-{i}"}, "output": "out", "duration_ms": 1}})
        seq += 1
    for chk in job["checks"]:
        events.append({"seq": seq, "ts": "2099-01-01T00:00:05.000Z", "actor": "core",
                       "type": "check.started", "data": {"name": chk["name"], "command": chk["command"]}})
        seq += 1
        events.append({"seq": seq, "ts": "2099-01-01T00:00:06.000Z", "actor": "core",
                       "type": "check.finished", "data": {"name": chk["name"], "exit_code": 0, "status": "passed"}})
        seq += 1
    events.append({"seq": seq, "ts": "2099-01-01T00:00:07.000Z", "actor": "core", "type": "run.finished",
                   "data": {"status": "completed", "pr_ready": True, "termination_reason": "completed"}})
    return events


def write_evidence(root: Path, *, mutate=None) -> dict:
    """Write a synthetic evidence dir; returns dict with paths and job."""
    root.mkdir(parents=True, exist_ok=True)
    job = make_job()
    prompt = build_prompt_v2(job)
    events = make_events(job, prompt)
    if mutate:
        job, prompt, events = mutate(job, prompt, events)
    manifest = {
        "schema_version": 1,
        "run_id": FIXTURE_RUN,
        "job_id": job["job_id"],
        "job_sha256": sha256_text(canonical_json(job)),
        "issue_snapshot_sha256": sha256_text(canonical_json(job["issue"])),
        "base_revision": job["base_revision"],
        "adapter": "dsh",
        "model": {"provider": "p", "id": "m"},
        "started_at": "2099-01-01T00:00:00.000Z",
        "artifacts": [],
        "status": "completed",
        "finished_at": "2099-01-01T00:01:00.000Z",
    }
    result = {"schema_version": 1, "run_id": FIXTURE_RUN, "job_id": job["job_id"],
              "status": "completed", "base_revision": job["base_revision"]}
    run_dir = root / "runs" / "fixture-batch" / FIXTURE_RUN
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    (run_dir / "job.snapshot.json").write_text(json.dumps(job))
    (run_dir / "result.json").write_text(json.dumps(result))
    (run_dir / "devflow-submission.json").write_text('{"schema_version":1}\n')
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (run_dir / "patch.diff.gz").write_bytes(b"fixture-gzip-bytes")
    (run_dir / "patch.diff").write_text("fixture patch bytes\n")
    commands = run_dir / "commands"
    commands.mkdir()
    (commands / "check-one.stdout.txt").write_text("one\n")
    (commands / "check-one.stderr.txt").write_text("")
    (commands / "check-two.stdout.txt").write_text("two\n")
    (commands / "check-two.stderr.txt").write_text("")
    (root / "README.md").write_text("# fixture evidence\n")

    entries = []
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_file() and rel not in ("README.md", "SHA256SUMS"):
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            entries.append(f"{digest}  {rel}")
    (root / "SHA256SUMS").write_text("\n".join(entries) + "\n")
    return {
        "run_dir": run_dir,
        "job": job,
        "prompt": prompt,
        "events": events,
        "manifest": manifest,
    }


def write_task_record(path: Path, job: dict) -> Path:
    task = {
        "schema_version": 1,
        "task_id": FIXTURE_JOB_ID,
        "repository": job["repository"],
        "base_revision": job["base_revision"],
        "issue": job["issue"],
        "scope": dict(job["constraints"], command_policy="unrestricted"),
        "execution": {"timeout_seconds": 100, "max_steps": 10, "max_command_seconds": 50},
        "checks": job["checks"],
        "runtime": job["runtime"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task))
    return path


def write_readiness(path: Path, task_record: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# readiness\n\n"
        f"task.json sha256 `{__import__('hashlib').sha256(task_record.read_bytes()).hexdigest()}`\n"
    )
    return path
