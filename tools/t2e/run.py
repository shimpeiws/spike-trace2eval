"""Single independent-agent run (issue #4).

Preflight requires valid evidence, source, environment, and leakage
records — absent or invalid prerequisites refuse launch and never consume
the experiment run. The run uses only permitted initial inputs and the
pinned source; raw evidence and evaluator-only artifacts stay outside the
agent's workspace.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .canonical import bounded, load_json, sha256_file
from .records import RecordState, require_record
from .materialize import _git

DEFAULT_TIMEOUT_S = 2400  # matches the original run's declared budget
SETUP_TIMEOUT_S = 900

PREREQUISITE_STAGES = ["evidence", "source", "environment", "leakage"]

# The original run's server credential lived only in this variable; the
# independent harness does not need it, so it is stripped and the
# stripping is recorded.
STRIPPED_ENV = ["OPENCODE_SERVER_PASSWORD"]


@dataclass
class Preflight:
    ok: bool
    missing: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)


def preflight(records_dir: Path) -> Preflight:
    pf = Preflight(ok=True)
    for stage in PREREQUISITE_STAGES:
        rec = require_record(records_dir, stage)
        if rec.state is RecordState.ABSENT:
            pf.missing.append(stage)
        elif rec.state is RecordState.INVALID:
            pf.invalid.append(f"{stage}: {'; '.join(rec.diagnostics)[:200]}")
    pf.ok = not pf.missing and not pf.invalid
    return pf


def prepare_workspace(case_agent_dir: Path, dest: Path, log_dir: Path, path_prefix: str | None = None) -> dict[str, Any]:
    """Copy the agent package into a disposable workspace and run setup.

    Dependencies are acquired here — before agent start — so that the
    post-acquisition phase runs with external retrieval restricted.
    """
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(case_agent_dir, dest, symlinks=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0"}
    if path_prefix:
        env["PATH"] = f"{path_prefix}:{env.get('PATH', '')}"
    proc = subprocess.run(
        ["sh", "-c", "cd source && corepack pnpm install --frozen-lockfile --ignore-scripts"],
        cwd=dest, capture_output=True, text=True, timeout=SETUP_TIMEOUT_S, env=env,
    )
    (log_dir / "run-setup.stdout.txt").write_text(proc.stdout[-20000:], encoding="utf-8")
    (log_dir / "run-setup.stderr.txt").write_text(proc.stderr[-20000:], encoding="utf-8")
    return {
        "exit_code": proc.returncode,
        "stdout_ref": {"file": "run-setup.stdout.txt", "sha256": sha256_file(log_dir / "run-setup.stdout.txt")},
        "stderr_ref": {"file": "run-setup.stderr.txt", "sha256": sha256_file(log_dir / "run-setup.stderr.txt")},
    }


def workspace_boundary_ok(workspace: Path, pinned_sha: str) -> list[str]:
    """Re-verify the handoff boundary on the live workspace copy."""
    problems: list[str] = []
    source = workspace / "source"
    try:
        remotes = [r for r in _git(source, "remote").stdout.split() if r]
        if remotes:
            problems.append(f"remotes configured: {remotes}")
        head = _git(source, "rev-parse", "HEAD").stdout.strip()
        if head != pinned_sha:
            problems.append(f"HEAD {head} != {pinned_sha}")
        count = int(_git(source, "rev-list", "--count", "HEAD").stdout.strip())
        if count != 1:
            problems.append(f"history has {count} commits")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
        problems.append(bounded(str(e), 200))
    for p in workspace.rglob("*"):
        if p.is_symlink() and not str(p.resolve()).startswith(str(workspace.resolve())):
            problems.append(f"escaping symlink: {p.relative_to(workspace)}")
            break
    return problems


OPENCODE_MODEL = "opencode-go/deepseek-v4.1-flash"  # the original run's model

# Workspace-level opencode config written before launch: auto-approve is
# passed on the command line; this file denies the retrieval surface
# (webfetch / paths outside the workspace) while inference stays usable.
OPENCODE_CONFIG = {
    "$schema": "https://opencode.ai/config.json",
    "permission": {
        "edit": "allow",
        "bash": "allow",
        "webfetch": "deny",
        "external_directory": "deny",
    },
    "tools": {"webfetch": False},
}


def harness_command(harness: str, source_dir: Path, task_text: str) -> list[str]:
    """Return argv for the supported independent harnesses (prompt placement varies)."""
    if harness == "claude":
        # Headless; external search/retrieval tools are denied while the
        # inference service itself remains usable. Prompt via stdin.
        return [
            "claude", "-p", "--dangerously-skip-permissions",
            "--disallowedTools", "WebSearch WebFetch",
            "--output-format", "text",
        ]
    if harness == "opencode":
        # Same harness family + provider + model as the original run.
        # --auto: unattended approval; --pure: no external plugins;
        # --format json: raw event stream retained as the run's trace;
        # webfetch/external_directory denied via workspace opencode.json.
        return [
            "opencode", "run", "--auto", "--pure", "--format", "json",
            "--model", OPENCODE_MODEL, "--dir", str(source_dir), task_text,
        ]
    raise ValueError(f"unsupported harness: {harness}")


def launch_agent(
    harness: str,
    workspace: Path,
    task_text: str,
    timeout_s: int,
    log_dir: Path,
    path_prefix: str | None = None,
) -> dict[str, Any]:
    """Launch the single experiment run. Exactly one attempt."""
    source_dir = workspace / "source"
    argv = harness_command(harness, source_dir, task_text)
    env = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
    if path_prefix:
        env["PATH"] = f"{path_prefix}:{env.get('PATH', '')}"
    log_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        proc = subprocess.run(
            argv, cwd=source_dir, env=env, input=task_text if harness == "claude" else None,
            capture_output=True, text=True, timeout=timeout_s,
        )
        termination = "completed" if proc.returncode == 0 else f"exit-{proc.returncode}"
        out, err = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        termination = "timeout"
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        err = (e.stderr or "") if isinstance(e.stderr, str) else ""
    except OSError as e:
        return {"termination_reason": "launch_failure", "error": bounded(str(e), 300), "duration_s": time.time() - started}
    (log_dir / "agent.stdout.txt").write_text(out, encoding="utf-8")
    (log_dir / "agent.stderr.txt").write_text(err, encoding="utf-8")
    return {
        "termination_reason": termination,
        "duration_s": round(time.time() - started, 1),
        "stdout_ref": {"file": "agent.stdout.txt", "sha256": sha256_file(log_dir / "agent.stdout.txt"), "bytes": len(out.encode())},
        "stderr_ref": {"file": "agent.stderr.txt", "sha256": sha256_file(log_dir / "agent.stderr.txt"), "bytes": len(err.encode())},
        "final_answer_tail": out[-4000:],
    }


# Harness-config files written into the workspace for the run are
# operational, not agent output — excluded from the collected patch.
WORKSPACE_OPERATIONAL_FILES = {"opencode.json"}


def collect_patch(source_dir: Path, artifacts_dir: Path) -> dict[str, Any]:
    """Capture the agent's produced diff + changed-file list (evaluator side)."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    _git(source_dir, "add", "-A")
    diff = subprocess.run(
        ["git", "-C", str(source_dir), "diff", "--cached", "--binary", "HEAD",
         "--", ".", ":(exclude)opencode.json"],
        capture_output=True, text=True, timeout=120,
    )
    patch_path = artifacts_dir / "patch.diff"
    patch_path.write_text(diff.stdout, encoding="utf-8")
    status = _git(source_dir, "status", "--porcelain").stdout
    changed = sorted(
        l[3:] for l in status.splitlines()
        if l.strip() and l[3:] not in WORKSPACE_OPERATIONAL_FILES
    )
    return {
        "patch_ref": {"file": "patch.diff", "sha256": sha256_file(patch_path), "bytes": len(diff.stdout.encode())},
        "changed_files": changed,
    }


def build_execution_record(
    *,
    harness: str,
    model: str,
    task_sha256: str,
    pinned_sha: str,
    setup: dict[str, Any],
    boundary_problems: list[str],
    launch: dict[str, Any],
    patch: dict[str, Any] | None,
    timeout_s: int,
    stripped_env: list[str],
    env_keys: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "stage": "execution",
        "status": "passed" if launch.get("termination_reason") == "completed" else "failed",
        "harness": harness,
        "model": model,
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "pinned_source": pinned_sha,
        "task_sha256": task_sha256,
        "budgets": {
            "timeout_seconds": timeout_s,
            "reference_max_steps": 80,
            "reference_max_command_seconds": 600,
            "step_limit_enforceable": False,
        },
        "setup": setup,
        "boundary_problems": boundary_problems,
        "launch": launch,
        "patch": patch,
        "env_keys": sorted(env_keys),
        "stripped_env": stripped_env,
        "deviations_from_original": [
            "harness: original used OpenCode 1.18.34 via a loopback server + dsh adapter; this run uses the configured independent harness directly",
            "harness system prompt and injected context are unrecoverable; operator-level agent configuration may differ",
            "network egress is restricted at the harness tool level (no web tools), not at OS level",
        ],
    }
