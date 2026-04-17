"""Unit tests for bulk-gh-pr-details.

Mocks subprocess.run so no real `gh` calls happen. Covers:
    - Spec parsing: valid + invalid formats.
    - Successful single-PR fetch.
    - gh nonzero exit handled as per-item error.
    - Malformed JSON from gh handled as per-item error.
    - Fan-out via run_cli preserves input order.

Run with:
    python3 -m unittest discover -s skills/bulk/tests -p 'test_*.py'
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Put the skill dir on sys.path so `import chop_bulk` works when tests
# are discovered via `unittest discover -s skills/bulk/tests`.
_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from chop_bulk.gh_pr_details import (  # noqa: E402
    fetch_pr,
    parse_spec,
    run_cli,
)


def _mk_result(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    """Build a subprocess.CompletedProcess-shaped mock."""
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


class TestParseSpec(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(parse_spec("idvorkin/chop-conventions#169"), ("idvorkin/chop-conventions", 169))

    def test_with_whitespace(self):
        self.assertEqual(parse_spec("  owner/repo#12  "), ("owner/repo", 12))

    def test_missing_hash(self):
        with self.assertRaises(ValueError):
            parse_spec("owner/repo")

    def test_missing_number(self):
        with self.assertRaises(ValueError):
            parse_spec("owner/repo#")

    def test_non_numeric_number(self):
        with self.assertRaises(ValueError):
            parse_spec("owner/repo#abc")

    def test_multiple_slashes(self):
        with self.assertRaises(ValueError):
            parse_spec("owner/sub/repo#1")

    def test_no_slash(self):
        with self.assertRaises(ValueError):
            parse_spec("repoonly#1")


class TestFetchPr(unittest.TestCase):
    def test_success(self):
        stdout = json.dumps(
            {
                "title": "feat: do thing",
                "state": "OPEN",
                "mergeable": "MERGEABLE",
                "mergeStateStatus": "CLEAN",
                "url": "https://github.com/o/r/pull/1",
            }
        )
        mock_run = MagicMock(return_value=_mk_result(stdout=stdout))
        out = fetch_pr("o/r#1", run=mock_run)
        self.assertEqual(out["repo"], "o/r")
        self.assertEqual(out["number"], 1)
        self.assertEqual(out["title"], "feat: do thing")
        self.assertEqual(out["state"], "OPEN")
        self.assertEqual(out["mergeable"], "MERGEABLE")
        self.assertEqual(out["mergeStateStatus"], "CLEAN")
        self.assertEqual(out["url"], "https://github.com/o/r/pull/1")
        self.assertNotIn("error", out)
        # The actual gh invocation shape.
        call_cmd = mock_run.call_args.args[0]
        self.assertEqual(call_cmd[0:3], ["gh", "pr", "view"])
        self.assertIn("o/r", call_cmd)
        self.assertIn("1", call_cmd)
        self.assertIn("--json", call_cmd)

    def test_parse_error_becomes_inline_error(self):
        mock_run = MagicMock()
        out = fetch_pr("malformed", run=mock_run)
        self.assertIn("error", out)
        self.assertIsNone(out["repo"])
        self.assertIsNone(out["number"])
        mock_run.assert_not_called()

    def test_gh_nonzero_exit_becomes_error(self):
        mock_run = MagicMock(
            return_value=_mk_result(stdout="", stderr="HTTP 404", returncode=1)
        )
        out = fetch_pr("o/r#99", run=mock_run)
        self.assertEqual(out["repo"], "o/r")
        self.assertEqual(out["number"], 99)
        self.assertEqual(out["error"], "HTTP 404")

    def test_gh_nonzero_exit_empty_stderr_synthesizes_error(self):
        mock_run = MagicMock(
            return_value=_mk_result(stdout="", stderr="", returncode=2)
        )
        out = fetch_pr("o/r#99", run=mock_run)
        self.assertIn("error", out)
        self.assertIn("gh exited 2", out["error"])

    def test_invalid_json_becomes_error(self):
        mock_run = MagicMock(
            return_value=_mk_result(stdout="not json", returncode=0)
        )
        out = fetch_pr("o/r#1", run=mock_run)
        self.assertIn("error", out)
        self.assertIn("invalid gh JSON", out["error"])

    def test_subprocess_raises_becomes_error(self):
        mock_run = MagicMock(side_effect=OSError("ENOENT gh"))
        out = fetch_pr("o/r#1", run=mock_run)
        self.assertIn("error", out)
        self.assertIn("OSError", out["error"])


class TestRunCli(unittest.TestCase):
    def test_empty_input_exits_2(self):
        # Capture stdout/stderr to keep the test run clean.
        with patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=io.StringIO()):
            rc = run_cli([])
        self.assertEqual(rc, 2)

    def test_fan_out_preserves_order(self):
        # Mock subprocess.run at the module global so the pure-function path
        # is exercised end-to-end (not via the `run=` injection).
        stdout_map = {
            "1": {"title": "one", "state": "OPEN", "mergeable": "MERGEABLE",
                  "mergeStateStatus": "CLEAN", "url": "u1"},
            "2": {"title": "two", "state": "CLOSED", "mergeable": "UNKNOWN",
                  "mergeStateStatus": "DIRTY", "url": "u2"},
            "3": {"title": "three", "state": "MERGED", "mergeable": "MERGEABLE",
                  "mergeStateStatus": "CLEAN", "url": "u3"},
        }

        def fake_run(cmd, **kwargs):
            # cmd = ['gh','pr','view','--repo','o/r','<N>','--json',FIELDS]
            number = cmd[5]
            return _mk_result(stdout=json.dumps(stdout_map[number]))

        captured = io.StringIO()
        with patch("chop_bulk.gh_pr_details.subprocess.run", side_effect=fake_run), \
             patch("sys.stdout", new=captured), \
             patch("sys.stderr", new=io.StringIO()):
            rc = run_cli(["o/r#1", "o/r#2", "o/r#3"], max_workers=3)
        self.assertEqual(rc, 0)
        parsed = json.loads(captured.getvalue())
        self.assertEqual([e["number"] for e in parsed], [1, 2, 3])
        self.assertEqual([e["title"] for e in parsed], ["one", "two", "three"])

    def test_partial_failure_does_not_fail_batch(self):
        def fake_run(cmd, **kwargs):
            number = cmd[5]
            if number == "2":
                return _mk_result(stdout="", stderr="not found", returncode=1)
            return _mk_result(
                stdout=json.dumps(
                    {"title": f"t{number}", "state": "OPEN", "mergeable": "MERGEABLE",
                     "mergeStateStatus": "CLEAN", "url": f"u{number}"}
                )
            )

        captured = io.StringIO()
        with patch("chop_bulk.gh_pr_details.subprocess.run", side_effect=fake_run), \
             patch("sys.stdout", new=captured), \
             patch("sys.stderr", new=io.StringIO()):
            rc = run_cli(["o/r#1", "o/r#2", "o/r#3"], max_workers=2)
        self.assertEqual(rc, 0)  # batch itself succeeded
        parsed = json.loads(captured.getvalue())
        self.assertEqual(len(parsed), 3)
        self.assertNotIn("error", parsed[0])
        self.assertEqual(parsed[1]["error"], "not found")
        self.assertNotIn("error", parsed[2])


if __name__ == "__main__":
    unittest.main()
