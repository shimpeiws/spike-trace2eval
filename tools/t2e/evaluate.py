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

import hashlib
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
# commit): supported version, a JSON result, exit 0. Additionally it
# appends the argv it received (the task body reaches the runtime as an
# argv element at the pinned commit) to a capture file so the evaluator
# can verify the body the runtime actually received. The capture path
# is baked into the script because the target allows only
# PATH/LANG/LC_ALL/TERM/TZ through to the runtime's environment.
def _claude_shim(capture: Path) -> str:
    return """#!/usr/bin/env node
const fs = require('fs');
const args = process.argv.slice(2);
if (args.includes('--version')) {
  process.stdout.write('2.1.0\\n');
  process.exit(0);
}
fs.appendFileSync(%s, JSON.stringify({argv: args}) + '\\n');
process.stdout.write(JSON.stringify({
  result: 'fixture response',
  usage: { input_tokens: 3, output_tokens: 2 },
  modelUsage: { 'claude-fixture-1': { inputTokens: 3, outputTokens: 2 } },
  total_cost_usd: 0.001,
}));
""" % json.dumps(str(capture))

TASK_CONTENT = "Reply using the fixture.\n"
# A different body for the digest-varies probe: an implementation that
# hardcodes the reference digest fails this check.
TASK_CONTENT_ALT = "Reply using the fixture.\nSecond probe line: t2e-alt.\n"


def expected_task_digest(content: str) -> str:
    """The trace contract: task.digest is sha256 of the task body bytes."""
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


_FILENAME_SUFFIX_RE = re.compile(r"\.[A-Za-z0-9]{1,10}$")


def task_source_is_symbolic(source: Any, project: Path) -> bool:
    """True when task.source is an honest symbolic origin (e.g. "stdin",
    "-", "inline") rather than a real or invented file path/name."""
    if not isinstance(source, str) or not source:
        return False
    if "/" in source or "\\" in source:
        return False
    if any(c.isspace() for c in source):
        return False
    if _FILENAME_SUFFIX_RE.search(source):
        return False
    if (project / source).exists():
        return False
    return True

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
    _write(shim, _claude_shim(bin_dir / "shim-capture.jsonl"), 0o755)
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


def _run_cli(target: Path, project: Path, args: list[str], stdin_text: str | None, timeout: int,
             path_prefix: str | None = None) -> dict[str, Any]:
    env = {
        **os.environ,
        "PATH": f"{project / 'bin'}:{os.environ.get('PATH', '')}",
        "HOME": str(project),
    }
    if path_prefix:
        env["PATH"] = f"{path_prefix}:{env['PATH']}"
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


def _captured_bodies(capture: Path) -> list[str]:
    """Task bodies the shim recorded receiving (one argv tail per call)."""
    if not capture.is_file():
        return []
    bodies: list[str] = []
    for line in capture.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            argv = (json.loads(line) or {}).get("argv") or []
        except json.JSONDecodeError:
            continue
        if argv:
            bodies.append(argv[-1])
    return bodies


def evaluate_behavior(target: Path, timeout: int = RUN_TIMEOUT_S,
                      path_prefix: str | None = None) -> BehaviorResult:
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

        # The shim records the body it received; cleared before each run so
        # each capture belongs to exactly one run.
        capture = project / "bin" / "shim-capture.jsonl"

        def invoke(args: list[str], stdin_text: str | None) -> dict[str, Any]:
            capture.unlink(missing_ok=True)
            out = _run_cli(target, project, args, stdin_text, timeout,
                           path_prefix=path_prefix)
            out["bodies"] = _captured_bodies(capture)
            return out

        expected_digest = expected_task_digest(TASK_CONTENT)

        # Baseline: the file-based form must work and yields the content
        # digest for identical bytes.
        file_task = project / ".yuurei" / "tasks" / "smoke.md"
        before = _snapshot_files(project)
        file_run = invoke(["run", "--profile", "fixture", "--task", str(file_task)], None)
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
        res.add("file-source-matches", file_task_info.get("source") == str(file_task),
                f"task.source={file_task_info.get('source')!r}")
        res.add("file-digest-is-content-hash", file_digest == expected_digest,
                f"recorded={file_digest} expected={expected_digest}")
        res.add("file-body-received", bool(file_run["bodies"]) and file_run["bodies"][-1] == TASK_CONTENT,
                f"received={file_run['bodies'][-1][:80]!r}" if file_run["bodies"] else "no shim capture")

        # Inline form: try each strategy until one executes.
        inline_run: dict[str, Any] | None = None
        strategy_used: dict[str, str] | None = None
        rejections: list[str] = []
        for strat in INLINE_STRATEGIES:
            args = ["run", "--profile", "fixture"] + [
                TASK_CONTENT if a == "__CONTENT__" else a for a in strat["args"]
            ]
            stdin_text = TASK_CONTENT if strat["via"] == "stdin" else None
            attempt = invoke(args, stdin_text)
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
        res.add("inline-body-received", bool(inline_run["bodies"]) and inline_run["bodies"][-1] == TASK_CONTENT,
                f"received={inline_run['bodies'][-1][:80]!r}" if inline_run["bodies"] else "no shim capture")

        # No file at all may appear inside the project as a side effect
        # (evaluator-owned bin/ and .yuurei/runs/ are excluded).
        after = _snapshot_files(project)
        new_files = sorted(after - before)
        res.add("no-task-file-created", not new_files, f"new files: {new_files[:10]}")

        inline_trace = _read_trace(project, inline_run["stdout"])
        inline_task = (inline_trace or {}).get("task") or {}
        inline_digest = inline_task.get("digest")
        res.add("digest-equal", inline_digest is not None and inline_digest == file_digest,
                f"file={file_digest} inline={inline_digest}")
        res.add("inline-digest-is-content-hash", inline_digest == expected_digest,
                f"recorded={inline_digest} expected={expected_digest}")

        # A different body must produce a different digest — a hardcoded
        # digest cannot pass this probe.
        alt_args = ["run", "--profile", "fixture"] + [
            TASK_CONTENT_ALT if a == "__CONTENT__" else a for a in strategy_used["args"]
        ]
        alt_stdin = TASK_CONTENT_ALT if strategy_used["via"] == "stdin" else None
        alt_run = invoke(alt_args, alt_stdin)
        alt_trace = _read_trace(project, alt_run["stdout"]) if alt_run["code"] == 0 else None
        alt_digest = ((alt_trace or {}).get("task") or {}).get("digest")
        expected_alt = expected_task_digest(TASK_CONTENT_ALT)
        res.add("digest-varies-with-input",
                alt_run["code"] == 0 and alt_digest == expected_alt,
                f"alt digest={alt_digest} expected={expected_alt}")

        # Honest provenance: a task with no path must record a symbolic
        # origin (e.g. "stdin", "-", "inline"), not a real or fake file.
        source = inline_task.get("source")
        res.add("provenance-honest", task_source_is_symbolic(source, project),
                f"task.source={source!r}")

        res.verdict = "satisfied" if all(c["ok"] for c in res.checks) else "unsatisfied"
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
