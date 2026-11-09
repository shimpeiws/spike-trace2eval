import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from t2e.materialize import materialize


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


if __name__ == "__main__":
    unittest.main()
