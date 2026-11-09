"""Agent-input provenance and leakage enforcement (issue #3, first half).

Validates the agent-visible input package — allowlist, hashes, provenance,
mount boundary, Git history, and external-solution access — and refuses
before execution when any check fails. Patch string overlap is computed
as supplementary information only: shared code is not proof of leakage.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .canonical import bounded, sha256_file
from .materialize import _git

# Files at the top of the agent package. Everything else must live under
# source/ (whose contents are pinned by the commit) or source/.git.
AGENT_TOP_LEVEL = {"task.md", "environment.json", "setup.sh", "source"}

# Artifact names from the retained run that must never appear in the
# agent package (the *names* are evaluator-side vocabulary).
FORBIDDEN_FILENAMES = {
    "patch.diff", "patch.diff.gz", "events.jsonl", "job.snapshot.json",
    "result.json", "devflow-submission.json", "devflow-submission-out.json",
    "manifest.json", "export-manifest.json", "SHA256SUMS",
}

# Content markers that indicate retained-run data bleeding into inputs.
FORBIDDEN_SUBSTRINGS = [
    "patch.diff",
    "events.jsonl",
    "job.snapshot",
    "devflow-submission",
    "OPENCODE_SERVER_PASSWORD",
    "credentials_ref",
]

TEXT_SUFFIXES = {".md", ".txt", ".json", ".sh", ".ts", ".yaml", ".yml", ".toml"}


@dataclass
class LeakageResult:
    ok: bool = True
    checks: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    supplementary: dict[str, Any] = field(default_factory=dict)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": ok, "detail": bounded(detail, 400)})
        if not ok:
            self.ok = False
            self.diagnostics.append(f"{name}: {bounded(detail, 300)}")


def _iter_package_files(agent_dir: Path):
    source = agent_dir / "source"
    for p in sorted(agent_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(agent_dir)
        if rel.parts[0] == "source" and len(rel.parts) > 1 and rel.parts[1] == ".git":
            continue  # git internals are verified structurally, not by name
        yield p, rel


def check_input_package(
    agent_dir: Path,
    expected_hashes: dict[str, str] | None = None,
    forbidden_path_strings: list[str] | None = None,
    pinned_sha: str | None = None,
    patch_file: Path | None = None,
) -> LeakageResult:
    res = LeakageResult()
    if not agent_dir.is_dir():
        res.add("package-present", False, f"not a directory: {agent_dir}")
        return res

    # --- allowlist ------------------------------------------------------
    top = {p.name for p in agent_dir.iterdir()}
    extra = sorted(top - AGENT_TOP_LEVEL)
    res.add("allowlist-top-level", not extra, f"unexpected entries: {extra}")

    filenames = [(rel, p) for p, rel in _iter_package_files(agent_dir)]
    # source/ contents are hash-pinned to the commit; forbidden-name checks
    # cover everything outside it (untracked files inside source/ are caught
    # by the git-clean check below).
    bad_names = [str(rel) for rel, p in filenames if rel.parts[0] != "source" and p.name in FORBIDDEN_FILENAMES]
    res.add("forbidden-filenames", not bad_names, f"{bad_names[:10]}")

    # --- symlinks / mount boundary --------------------------------------
    links = [str(p.relative_to(agent_dir)) for p in agent_dir.rglob("*") if p.is_symlink()]
    external = []
    for rel in links:
        target = (agent_dir / rel).resolve()
        if not str(target).startswith(str(agent_dir.resolve())):
            external.append(rel)
    res.add("mount-boundary", not external, f"symlinks escaping package: {external[:10]}")

    # --- hashes ----------------------------------------------------------
    if expected_hashes is not None:
        mismatched = []
        for rel, digest in expected_hashes.items():
            target = agent_dir / rel
            if not target.is_file() or sha256_file(target) != digest:
                mismatched.append(rel)
        res.add("input-hashes", not mismatched, f"mismatch/missing: {mismatched[:10]}")

    # --- content scan (agent-visible text only; source/ is pinned) -------
    hits: list[str] = []
    haystacks = forbidden_path_strings or []
    for rel, p in filenames:
        if rel.parts[0] == "source":
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for marker in FORBIDDEN_SUBSTRINGS + haystacks:
            if marker and marker in text:
                hits.append(f"{rel}: contains {marker!r}")
    res.add("content-scan", not hits, "; ".join(hits[:10]))

    # --- git history boundary --------------------------------------------
    source = agent_dir / "source"
    if source.is_dir():
        git_dir = source / ".git"
        if not git_dir.exists():
            res.add("git-history", False, "source has no .git (expected a depth-1 materialized repo)")
        else:
            try:
                count = int(_git(source, "rev-list", "--count", "HEAD").stdout.strip())
                remotes = [r for r in _git(source, "remote").stdout.split() if r]
                ok = count == 1 and not remotes
                res.add("git-history", ok, f"commits={count} remotes={remotes}")
                if pinned_sha:
                    head = _git(source, "rev-parse", "HEAD").stdout.strip()
                    res.add("git-pinned-head", head == pinned_sha, f"HEAD={head}")
                status = _git(source, "status", "--porcelain").stdout.strip()
                res.add("git-clean", status == "", f"dirty: {status.splitlines()[:5]}")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
                res.add("git-history", False, bounded(str(e), 300))

    # --- supplementary: patch string overlap (never pass/fail) -----------
    if patch_file is not None and patch_file.is_file():
        patch_text = patch_file.read_text(encoding="utf-8", errors="replace")
        added = [l[1:] for l in patch_text.splitlines() if l.startswith("+") and not l.startswith("+++")]
        added_nonblank = [l.strip() for l in added if len(l.strip()) >= 12]
        overlap = 0
        task_text = ""
        task_path = agent_dir / "task.md"
        if task_path.is_file():
            task_text = task_path.read_text(encoding="utf-8", errors="replace")
        for line in added_nonblank:
            if line in task_text:
                overlap += 1
        res.supplementary["patch_overlap"] = {
            "added_lines_checked": len(added_nonblank),
            "found_in_task_md": overlap,
            "note": "supplementary only — common code is not proof of leakage",
        }
    return res
