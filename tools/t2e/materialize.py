"""Pinned-source materialization (issue #2).

Recreates the starting source tree from the pinned commit only: a
depth-1 fetch of exactly the requested SHA into a fresh repository — no
future commits, no remote configuration, no original workspace paths —
then verifies the tracked contents against git's content-addressed
objects. The source repository is only ever read.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .canonical import bounded

GIT_TIMEOUT_S = 120

# .git files that record operator-local metadata (fetch source path,
# reflog identities) rather than pinned-commit data — removed after
# materialization and forbidden by the package checks.
GIT_OPERATOR_METADATA = ("FETCH_HEAD", "ORIG_HEAD", "MERGE_HEAD")

# Textual .git metadata that must never reference the operator host:
# absolute filesystem paths and file URLs. Git objects under objects/
# are content-addressed pinned data (including upstream committer
# identities) and are legitimately part of the source.
HOST_REFERENCE_RE = re.compile(
    r"/Users/|/home/|/private/|/var/|/tmp/|file://|[A-Za-z]:\\"
)


@dataclass
class MaterializeResult:
    ok: bool
    head: str = ""
    file_count: int = 0
    commit_count: int = 0
    remotes: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> "MaterializeResult":
        self.ok = False
        self.diagnostics.append(bounded(msg, 400))
        return self


def _git(dest: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(dest), *args],
        capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=check,
    )


def scrub_git_metadata(dest: Path) -> list[str]:
    """Remove operator-local .git metadata (fetch/reflog artifacts)."""
    git_dir = dest / ".git"
    removed: list[str] = []
    for name in GIT_OPERATOR_METADATA:
        p = git_dir / name
        if p.exists():
            p.unlink()
            removed.append(name)
    logs = git_dir / "logs"
    if logs.is_dir():
        shutil.rmtree(logs)
        removed.append("logs/")
    return removed


def git_hygiene_problems(dest: Path, source_ref: str | None = None) -> list[str]:
    """Detect residual operator-host references inside .git metadata."""
    problems: list[str] = []
    git_dir = dest / ".git"
    for name in GIT_OPERATOR_METADATA + ("logs",):
        if (git_dir / name).exists():
            problems.append(f".git/{name} present (operator metadata)")
    for p in sorted(git_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(git_dir)
        if rel.parts[0] in ("objects", "hooks"):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if HOST_REFERENCE_RE.search(text):
            problems.append(f".git/{rel} contains a host path or file URL")
        if source_ref and source_ref in text:
            problems.append(f".git/{rel} contains the source repository path")
    return problems


def _verify(dest: Path, sha: str, res: MaterializeResult, source_ref: str | None = None) -> MaterializeResult:
    hygiene = git_hygiene_problems(dest, source_ref)
    if hygiene:
        return res.fail(f"git metadata hygiene: {'; '.join(hygiene[:5])}")
    res.head = _git(dest, "rev-parse", "HEAD").stdout.strip()
    if res.head != sha:
        return res.fail(f"HEAD {res.head} != pinned {sha}")
    status = _git(dest, "status", "--porcelain").stdout
    if status.strip():
        return res.fail(f"worktree not clean: {status.splitlines()[:5]}")
    res.commit_count = int(_git(dest, "rev-list", "--count", "HEAD").stdout.strip())
    if res.commit_count != 1:
        return res.fail(f"history has {res.commit_count} commits; depth-1 materialization must show exactly 1")
    res.remotes = [r for r in _git(dest, "remote").stdout.split() if r]
    if res.remotes:
        return res.fail(f"remotes configured: {res.remotes}")
    res.file_count = len(_git(dest, "ls-files").stdout.splitlines())
    if res.file_count == 0:
        return res.fail("no tracked files materialized")
    # Content addressing: clean status + HEAD==sha means the worktree equals
    # the pinned tree; ls-tree cross-check guards a corrupt object store.
    tree_head = _git(dest, "rev-parse", "HEAD^{tree}").stdout.strip()
    pinned_tree = subprocess.run(
        ["git", "-C", str(dest), "rev-list", "-1", "HEAD"], capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
    )
    if pinned_tree.returncode != 0:
        return res.fail(bounded(pinned_tree.stderr, 300))
    res.ok = True
    res.diagnostics.append(f"tree {tree_head[:16]}… verified; {res.file_count} tracked files")
    return res


def materialize(repo: Path, sha: str, dest: Path) -> MaterializeResult:
    """Materialize the pinned commit's tracked files into `dest`.

    `dest` must not exist yet. `repo` may be a local path (preferred —
    read-only object access, never touches its checkout) or a URL.
    """
    res = MaterializeResult(ok=False)
    if dest.exists():
        return res.fail(f"destination exists: {dest}")
    dest.mkdir(parents=True)
    try:
        _git(dest, "init", "-q")
        # No reflogs: they record the operator's git identity and checkout
        # narration into the agent-visible repository.
        _git(dest, "config", "core.logAllRefUpdates", "false")
        # Fetch by path/URL: no remote is configured, so none can leak into
        # the agent-visible repository. FETCH_HEAD (which records the fetch
        # source) is scrubbed below.
        fetch = subprocess.run(
            ["git", "-C", str(dest), "fetch", "-q", "--depth=1", str(repo), sha],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
        )
        if fetch.returncode != 0:
            return res.fail(f"fetch failed: {bounded(fetch.stderr, 300)}")
        co = subprocess.run(
            ["git", "-C", str(dest), "checkout", "-q", "-b", "case-source", "FETCH_HEAD"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
        )
        if co.returncode != 0:
            return res.fail(f"checkout failed: {bounded(co.stderr, 300)}")
        scrub_git_metadata(dest)
        return _verify(dest, sha, res, source_ref=str(repo))
    except subprocess.TimeoutExpired:
        return res.fail("git operation timed out")
