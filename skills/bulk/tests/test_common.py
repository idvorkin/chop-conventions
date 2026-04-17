"""Unit tests for chop_bulk.common — the shared contract.

Covers:
    - read_inputs priority: positional > input-file > stdin.
    - read_inputs rejects non-array input.
    - parallel_map preserves input order.
    - parallel_map catches worker exceptions as inline errors.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from chop_bulk.common import parallel_map, read_inputs  # noqa: E402


class TestReadInputs(unittest.TestCase):
    def test_positional_wins(self):
        self.assertEqual(read_inputs(["a", "b"], None), ["a", "b"])

    def test_input_file_is_used(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(["x", "y", "z"], f)
            path = f.name
        try:
            self.assertEqual(read_inputs(None, path), ["x", "y", "z"])
        finally:
            Path(path).unlink()

    def test_input_file_rejects_non_array(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"not": "array"}, f)
            path = f.name
        try:
            with self.assertRaises(ValueError):
                read_inputs(None, path)
        finally:
            Path(path).unlink()

    def test_tty_stdin_raises(self):
        fake_stdin = io.StringIO("")
        fake_stdin.isatty = lambda: True  # type: ignore[assignment]
        with patch("sys.stdin", new=fake_stdin):
            with self.assertRaises(ValueError):
                read_inputs(None, None)

    def test_stdin_array(self):
        fake_stdin = io.StringIO('["p","q"]')
        fake_stdin.isatty = lambda: False  # type: ignore[assignment]
        with patch("sys.stdin", new=fake_stdin):
            self.assertEqual(read_inputs(None, None), ["p", "q"])

    def test_stdin_non_array_raises(self):
        fake_stdin = io.StringIO("42")
        fake_stdin.isatty = lambda: False  # type: ignore[assignment]
        with patch("sys.stdin", new=fake_stdin):
            with self.assertRaises(ValueError):
                read_inputs(None, None)


class TestParallelMap(unittest.TestCase):
    def test_order_preserved(self):
        def worker(x):
            return {"input": x, "doubled": x * 2}

        out = parallel_map([1, 2, 3, 4], worker, max_workers=4)
        self.assertEqual([r["input"] for r in out], [1, 2, 3, 4])
        self.assertEqual([r["doubled"] for r in out], [2, 4, 6, 8])

    def test_worker_exception_becomes_inline_error(self):
        def worker(x):
            if x == "boom":
                raise RuntimeError("kaboom")
            return {"ok": x}

        out = parallel_map(["a", "boom", "b"], worker, max_workers=2)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0], {"ok": "a"})
        self.assertIn("error", out[1])
        self.assertIn("RuntimeError", out[1]["error"])
        self.assertEqual(out[2], {"ok": "b"})

    def test_empty_list(self):
        out = parallel_map([], lambda x: {"x": x})
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
