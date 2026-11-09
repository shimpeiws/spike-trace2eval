import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from t2e.canonical import bounded, canonical_json, sha256_text


class TestCanonical(unittest.TestCase):
    def test_sorted_keys_recursive(self):
        v = {"b": 1, "a": {"z": 1, "y": [{"b": 2, "a": 3}]}}
        self.assertEqual(canonical_json(v), '{"a":{"y":[{"a":3,"b":2}],"z":1},"b":1}')

    def test_matches_js_json_stringify(self):
        # JS JSON.stringify: compact, non-ASCII literal, no escaping of non-ASCII.
        v = {"k": "日本語", "arr": [1, "x"]}
        self.assertEqual(canonical_json(v), '{"arr":[1,"x"],"k":"日本語"}')

    def test_sha256_vector(self):
        self.assertEqual(
            sha256_text(""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

    def test_bounded(self):
        self.assertEqual(bounded("abc", 10), "abc")
        self.assertTrue(bounded("x" * 5000).endswith("chars]"))
        self.assertLess(len(bounded("x" * 5000)), 2100)


if __name__ == "__main__":
    unittest.main()
