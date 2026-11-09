import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from t2e.materialize import git_hygiene_problems, materialize, scrub_git_metadata


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


class TestMaterialize(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t2e-mat-"))
        self.repo = self.tmp / "origin-repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        (self.repo / "a.txt").write_text("one\n")
        (self.repo / "dir").mkdir()
        (self.repo / "dir" / "b.txt").write_text("two\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "first")
        self.sha = _git(self.repo, "rev-parse", "HEAD")
        (self.repo / "later.txt").write_text("future\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "second")

    def test_materialize_pinned_tree(self):
        dest = self.tmp / "out"
        res = materialize(self.repo, self.sha, dest)
        self.assertTrue(res.ok, res.diagnostics)
        self.assertEqual(res.head, self.sha)
        self.assertEqual(res.commit_count, 1)
        self.assertEqual(res.remotes, [])
        self.assertEqual(res.file_count, 2)
        self.assertEqual((dest / "a.txt").read_text(), "one\n")
        self.assertFalse((dest / "later.txt").exists())
        # Source repo untouched: still on its own HEAD with both commits.
        self.assertEqual(int(_git(self.repo, "rev-list", "--count", "HEAD")), 2)

    def test_dest_exists_refused(self):
        dest = self.tmp / "occupied"
        dest.mkdir()
        res = materialize(self.repo, self.sha, dest)
        self.assertFalse(res.ok)

    def test_bad_sha_fails(self):
        res = materialize(self.repo, "f" * 40, self.tmp / "x")
        self.assertFalse(res.ok)

    def test_setup_outputs_separate(self):
        dest = self.tmp / "out2"
        res = materialize(self.repo, self.sha, dest)
        self.assertTrue(res.ok, res.diagnostics)
        # node_modules produced later is untracked — never part of source identity.
        (dest / "node_modules").mkdir()
        (dest / "node_modules" / "x.js").write_text("x")
        tracked = _git(dest, "ls-files").splitlines()
        self.assertNotIn("node_modules/x.js", tracked)

    def test_materialized_package_contains_no_host_paths(self):
        """FIND-001: .git carries no fetch source path, reflog, or identity."""
        dest = self.tmp / "out3"
        res = materialize(self.repo, self.sha, dest)
        self.assertTrue(res.ok, res.diagnostics)
        self.assertFalse((dest / ".git" / "FETCH_HEAD").exists())
        self.assertFalse((dest / ".git" / "ORIG_HEAD").exists())
        self.assertFalse((dest / ".git" / "logs").exists())
        # The operator's source path and git identity appear nowhere in .git.
        self.assertEqual(git_hygiene_problems(dest, str(self.repo)), [])
        for p in (dest / ".git").rglob("*"):
            if p.is_file():
                self.assertNotIn(b"t@t", p.read_bytes())

    def test_git_hygiene_flags_leftover_fetch_head(self):
        """FIND-001: a FETCH_HEAD holding the source path is detected."""
        dest = self.tmp / "dirty"
        dest.mkdir()
        _git(dest, "init", "-q")
        (dest / ".git" / "FETCH_HEAD").write_text(
            f"{'a' * 40}\t\t'{self.sha}' of {self.repo}\n")
        problems = git_hygiene_problems(dest, str(self.repo))
        self.assertTrue(any("FETCH_HEAD" in p for p in problems))
        self.assertTrue(any("host path" in p or "source repository" in p for p in problems))

    def test_git_hygiene_flags_reflog(self):
        dest = self.tmp / "dirty2"
        dest.mkdir()
        _git(dest, "init", "-q")
        (dest / ".git" / "logs").mkdir()
        (dest / ".git" / "logs" / "HEAD").write_text(
            f"{'0' * 40} {self.sha} op <op@host> 1 +0000\tcheckout\n")
        self.assertTrue(git_hygiene_problems(dest))

    def test_scrub_removes_metadata(self):
        dest = self.tmp / "scrub"
        dest.mkdir()
        _git(dest, "init", "-q")
        (dest / ".git" / "FETCH_HEAD").write_text("x")
        (dest / ".git" / "logs").mkdir()
        (dest / ".git" / "logs" / "HEAD").write_text("x")
        removed = scrub_git_metadata(dest)
        self.assertIn("FETCH_HEAD", removed)
        self.assertIn("logs/", removed)
        self.assertEqual(git_hygiene_problems(dest), [])


if __name__ == "__main__":
    unittest.main()
