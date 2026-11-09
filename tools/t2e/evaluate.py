"""Evaluator-side behavioral checks (issue #3, second half).

Drives the materialized target through its own CLI with a fixture runtime
— the same shape as the target's test harness — and verifies behavior
derived from the initial requirements, independently of the original
patch:

1. executing a string task without creating a task file in the project,
2. digest equality with identical file input,
3. honest task provenance in the trace.

stdin or equivalent inline forms are supported; the original
implementation's internal structure is not required. Verification uses
fixture runtimes — no real model inference.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .canonical import bounded

RUN_TIMEOUT_S = 120
RUNTIME_ID = "claude-code"
RUNTIME_COMMAND = "claude"

# Deterministic `claude` shim — same contract as the target's own
# test/harness/cli-fixture.ts CLAUDE_SHIM (public source at the pinned
# commit): supported version, a JSON result, exit 0.
CLAUDE_SHIM = """#!/usr/bin/env node
const args = process.argv.slice(2);
if (args.includes('--version')) {
  process.stdout.write('2.1.0\\n');
  process.exit(0);
}
process.stdout.write(JSON.stringify({
  result: 'fixture response',
  usage: { input_tokens: 3, output_tokens: 2 },
  modelUsage: { 'claude-fixture-1': { inputTokens: 3, outputTokens: 2 } },
  total_cost_usd: 0.001,
}));
"""

TASK_CONTENT = "Reply using the fixture.\n"

# Invocation strategies for "stdin or an equivalent inline form", tried
# in order. The first that succeeds is used; the chosen strategy is
# recorded so results stay interpretable.
INLINE_STRATEGIES = [
    {"name": "task-dash-stdin", "args": ["--task", "-"], "via": "stdin"},
    {"name": "task-stdin-flag", "args": ["--task-stdin"], "via": "stdin"},
    {"name": "task-text-inline", "args": ["--task-text", "__CONTENT__"], "via": "argv"},
]


@dataclass
class BehaviorResult:
    verdict: str = "inconclusive"  # satisfied | unsatisfied | inconclusive:<reason>
    checks: list[dict[str, Any]] = field(default_factory=list)
    strategy_used: str | None = None
    diagnostics: list[str] = field(default_factory=list)

    def add(self, name: str, ok: bool | None, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": ok, "detail": bounded(detail, 400)})
        return bool(ok)


def _write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def _make_fixture_project(root: Path) -> dict[str, Any]:
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    shim = bin_dir / RUNTIME_COMMAND
    _write(shim, CLAUDE_SHIM, 0o755)
    ydir = root / ".yuurei"
    _write(ydir / "profiles" / "fixture" / "profile.yaml", f"runtime: {RUNTIME_ID}\n")
    _write(ydir / "profiles" / "fixture" / "config" / "config.json", '{"fixture":1}\n')
    _write(ydir / "tasks" / "smoke.md", TASK_CONTENT)
    _write(
        ydir / "yuurei.yaml",
        "version: 1\n"
        "profiles:\n"
        "  fixture:\n"
        f"    runtime: {RUNTIME_ID}\n"
        "    source: ./profiles/fixture\n"
        "runs:\n"
        "  smoke:\n"
        "    profile: fixture\n"
        "    task: ./tasks/smoke.md\n",
    )
    return {"bin": bin_dir, "yuurei": ydir}


def _snapshot_files(root: Path) -> set[str]:
    out: set[str] = set()
    for p in root.rglob("*"):
        rel = str(p.relative_to(root))
        if rel.startswith("bin/") or rel.startswith(".yuurei/runs/"):
            continue
        if p.is_file() or p.is_symlink():
            out.add(rel)
    return out


def _run_cli(target: Path, project: Path, args: list[str], stdin_text: str | None, timeout: int) -> dict[str, Any]:
    env = {
        **os.environ,
        "PATH": f"{project / 'bin'}:{os.environ.get('PATH', '')}",
        "HOME": str(project),
    }
    cmd = [
        "node", "--import", str(target / "node_modules" / "tsx" / "dist" / "loader.mjs"),
        str(target / "src" / "index.ts"), *args,
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=project, env=env, input=stdin_text,
            capture_output=True, text=True, timeout=timeout,
        )
        return {"code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except subprocess.TimeoutExpired:
        return {"code": None, "stdout": "", "stderr": f"timeout after {timeout}s"}
    except OSError as e:
        return {"code": None, "stdout": "", "stderr": str(e)}


def _run_id(stdout: str) -> str | None:
    m = re.search(r"run ([^ ]+) finished", stdout)
    return m.group(1) if m else None


def _read_trace(project: Path, stdout: str) -> dict[str, Any] | None:
    rid = _run_id(stdout)
    if not rid:
        return None
    path = project / ".yuurei" / "runs" / rid / "trace.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def evaluate_behavior(target: Path, timeout: int = RUN_TIMEOUT_S) -> BehaviorResult:
    """Run the requirement-derived behavior checks against `target`.

    `target` must be a materialized yuurei tree with dependencies already
    installed (node_modules present).
    """
    res = BehaviorResult()
    if not (target / "node_modules" / "tsx" / "dist" / "loader.mjs").is_file():
        res.verdict = "inconclusive:target has no installed dependencies (node_modules/tsx missing)"
        return res
    if not (target / "src" / "index.ts").is_file():
        res.verdict = "inconclusive:target has no src/index.ts"
        return res

    with tempfile.TemporaryDirectory(prefix="t2e-eval-") as tmp:
        project = Path(tmp) / "project"
        project.mkdir()
        _make_fixture_project(project)

        # Baseline: the file-based form must work and yields the reference
        # digest for identical content.
        file_task = project / ".yuurei" / "tasks" / "smoke.md"
        before = _snapshot_files(project)
        file_run = _run_cli(target, project, ["run", "--profile", "fixture", "--task", str(file_task)], None, timeout)
        if file_run["code"] != 0:
            res.verdict = f"inconclusive:file-task baseline failed: {bounded(file_run['stderr'] or file_run['stdout'], 300)}"
            return res
        file_trace = _read_trace(project, file_run["stdout"])
        if not file_trace:
            res.verdict = "inconclusive:no trace for file-task run"
            return res
        file_task_info = (file_trace.get("task") or {})
        file_digest = file_task_info.get("digest")
        res.add("baseline-file-run", file_digest is not None, f"digest={file_digest}")

        # Inline form: try each strategy until one executes.
        inline_run: dict[str, Any] | None = None
        strategy_used: dict[str, str] | None = None
        rejections: list[str] = []
        for strat in INLINE_STRATEGIES:
            args = ["run", "--profile", "fixture"] + [
                TASK_CONTENT if a == "__CONTENT__" else a for a in strat["args"]
            ]
            stdin_text = TASK_CONTENT if strat["via"] == "stdin" else None
            attempt = _run_cli(target, project, args, stdin_text, timeout)
            trace = _read_trace(project, attempt["stdout"]) if attempt["code"] == 0 else None
            if attempt["code"] == 0 and trace is not None:
                inline_run, strategy_used = attempt, strat
                break
            rejections.append(f"{strat['name']}: code={attempt['code']} {bounded(attempt['stderr'] or attempt['stdout'], 150)}")
        res.strategy_used = strategy_used["name"] if strategy_used else None
        if inline_run is None:
            res.add("inline-task-executes", False, "all inline strategies rejected: " + " | ".join(rejections))
            res.verdict = "unsatisfied"
            return res
        res.add("inline-task-executes", True, f"strategy={res.strategy_used}")

        # No task file may appear inside the project as a side effect.
        after = _snapshot_files(project)
        new_files = sorted(after - before)
        task_like = [f for f in new_files if f.startswith(".yuurei/tasks/") or f.endswith(".md")]
        res.add("no-task-file-created", not task_like, f"new files: {new_files[:10]}")

        inline_trace = _read_trace(project, inline_run["stdout"])
        inline_task = (inline_trace or {}).get("task") or {}
        inline_digest = inline_task.get("digest")
        res.add("digest-equal", inline_digest is not None and inline_digest == file_digest,
                f"file={file_digest} inline={inline_digest}")

        # Honest provenance: a task with no path must record a symbolic
        # origin (e.g. "stdin", "-", "inline"), not a real or fake path.
        source = inline_task.get("source")
        source_is_honest = (
            isinstance(source, str)
            and len(source) > 0
            and "/" not in source
            and source != str(file_task)
        )
        res.add("provenance-honest", source_is_honest, f"task.source={source!r}")

        ok = all(c["ok"] for c in res.checks if c["name"] != "baseline-file-run")
        res.verdict = "satisfied" if ok else "unsatisfied"
        return res


def classify_control(verdict: str) -> str:
    return "satisfied" if verdict == "satisfied" else ("unsatisfied" if verdict.startswith("unsatisfied") else "inconclusive")


def apply_patch_to_copy(source: Path, patch: Path, dest: Path) -> str | None:
    """Copy `source` (with node_modules) to `dest` and apply a patch.

    Evaluator-side only: the positive-control patch never enters an
    agent-visible directory.
    """
    shutil.copytree(source, dest, symlinks=True)
    proc = subprocess.run(
        ["git", "-C", str(dest), "apply", "--whitespace=nowarn", str(patch)],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        return bounded(proc.stderr, 300)
    return None
