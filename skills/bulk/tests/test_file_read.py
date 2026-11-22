"""Unit tests for bulk-file-read.

Everything runs against a tempdir. The FIFO test would hang forever on a
regression (open() on a writer-less FIFO blocks), so it runs the read on a
daemon thread with a timeout instead of calling it inline.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from chop_bulk import file_read  # noqa: E402
from chop_bulk.file_read import _read_one, run_cli  # noqa: E402


class TestReadOne(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_reads_small_utf8_file(self):
        p = self.dir / "a.txt"
        p.write_text("héllo", encoding="utf-8")
        out = _read_one(str(p), 100)
        self.assertEqual(out["content_utf8_or_null"], "héllo")
        self.assertIsNone(out["error_or_null"])
        self.assertEqual(out["size_bytes"], 6)

    def test_file_exactly_at_limit_is_read(self):
        p = self.dir / "a.txt"
        p.write_bytes(b"x" * 10)
        self.assertEqual(_read_one(str(p), 10)["content_utf8_or_null"], "x" * 10)

    def test_oversize_file_skipped_by_stat(self):
        p = self.dir / "big.txt"
        p.write_bytes(b"x" * 11)
        out = _read_one(str(p), 10)
        self.assertIsNone(out["content_utf8_or_null"])
        self.assertIn("exceeds max-bytes", out["error_or_null"])

    def test_read_is_bounded_when_stat_underreports(self):
        """st_size lies (procfs, a file growing after stat): never read past the cap."""
        p = self.dir / "grows.txt"
        p.write_bytes(b"x" * 500)
        real_stat = os.stat(p)
        fake = os.stat_result((real_stat.st_mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        requested: list[int] = []
        real_open = open

        def spy_open(*a, **k):
            f = real_open(*a, **k)
            real_read = f.read

            class Spy:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    f.close()

                def read(self_inner, n=-1):
                    requested.append(n)
                    return real_read(n)

            return Spy()

        with (
            patch.object(file_read.os, "stat", return_value=fake),
            patch.object(file_read, "open", spy_open, create=True),
        ):
            out = _read_one(str(p), 10)
        self.assertEqual(requested, [11])
        self.assertIsNone(out["content_utf8_or_null"])
        self.assertIn("exceeds max-bytes", out["error_or_null"])

    def test_dev_zero_is_skipped_not_read(self):
        if not os.path.exists("/dev/zero"):
            self.skipTest("no /dev/zero")
        out = _read_one("/dev/zero", 10)
        self.assertEqual(out["error_or_null"], "skipped: not a regular file")
        self.assertIsNone(out["content_utf8_or_null"])

    def test_fifo_is_skipped_without_blocking(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("no mkfifo")
        fifo = self.dir / "pipe"
        os.mkfifo(fifo)
        box: dict = {}
        t = threading.Thread(
            target=lambda: box.update(out=_read_one(str(fifo), 10)), daemon=True
        )
        t.start()
        t.join(timeout=5)
        if t.is_alive():
            # Unblock the stuck open() so the thread can exit, then fail.
            fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
            self.fail("_read_one blocked on a FIFO")
        self.assertEqual(box["out"]["error_or_null"], "skipped: not a regular file")

    def test_directory_is_skipped(self):
        out = _read_one(str(self.dir), 10)
        self.assertEqual(out["error_or_null"], "skipped: not a regular file")

    def test_missing_file(self):
        out = _read_one(str(self.dir / "nope"), 10)
        self.assertIn("stat failed", out["error_or_null"])

    def test_non_utf8(self):
        p = self.dir / "bin"
        p.write_bytes(b"\xff\xfe\x00")
        self.assertIn("not UTF-8", _read_one(str(p), 10)["error_or_null"])

    def test_non_string_inputs_return_error(self):
        for bad in (123, None, ["a"], {"path": "x"}, 1.5, True):
            out = _read_one(bad, 10)  # type: ignore[arg-type]
            self.assertIn("invalid path", out["error_or_null"], bad)


class TestRunCli(unittest.TestCase):
    def test_non_string_elements_are_per_item_errors_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "a.txt"
            good.write_text("ok")
            buf = io.StringIO()
            with patch("sys.stdout", buf), patch("sys.stderr", io.StringIO()):
                rc = run_cli([str(good), ["x"], {"k": 1}, 7, None])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertEqual(out[str(good)]["content_utf8_or_null"], "ok")
        self.assertEqual(len(out["_error"]), 4)
        for entry in out["_error"]:
            self.assertIn("invalid path", entry["error_or_null"])

    def test_empty_input_returns_2(self):
        with patch("sys.stderr", io.StringIO()):
            self.assertEqual(run_cli([]), 2)


if __name__ == "__main__":
    unittest.main()
