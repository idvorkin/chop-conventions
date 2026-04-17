"""Unit tests for bulk-bd-show.

Mocks subprocess.run so no real `bd` calls happen. Covers:
    - normalize_bead slices parent/blocks/blocked_by out of dependencies.
    - fetch_bead unwraps `bd show --json`'s single-element array.
    - fetch_bead handles the object-not-array response shape too.
    - bd nonzero exit handled as per-item error.
    - run_cli preserves input order on fan-out.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from chop_bulk.bd_show import (  # noqa: E402
    fetch_bead,
    normalize_bead,
    run_cli,
)


def _mk_result(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


class TestNormalizeBead(unittest.TestCase):
    def test_plain_fields(self):
        raw = {
            "id": "proj-abc",
            "title": "Do thing",
            "status": "open",
            "priority": 1,
            "issue_type": "task",
            "dependencies": [],
        }
        out = normalize_bead(raw, "proj-abc")
        self.assertEqual(out["id"], "proj-abc")
        self.assertEqual(out["title"], "Do thing")
        self.assertEqual(out["status"], "open")
        self.assertEqual(out["priority"], 1)
        self.assertEqual(out["type"], "task")
        self.assertIsNone(out["parent"])
        self.assertEqual(out["blocks"], [])
        self.assertEqual(out["blocked_by"], [])

    def test_parent_resolved_from_dependencies(self):
        raw = {
            "id": "child-1",
            "title": "Child",
            "dependencies": [
                {"type": "parent-child", "source": "parent-1", "target": "child-1"},
            ],
        }
        out = normalize_bead(raw, "child-1")
        self.assertEqual(out["parent"], "parent-1")

    def test_blocks_and_blocked_by(self):
        raw = {
            "id": "b2",
            "title": "Middle",
            "dependencies": [
                {"type": "blocks", "source": "b2", "target": "b3"},
                {"type": "blocks", "source": "b1", "target": "b2"},
            ],
        }
        out = normalize_bead(raw, "b2")
        self.assertEqual(out["blocks"], ["b3"])
        self.assertEqual(out["blocked_by"], ["b1"])

    def test_fallback_type_key(self):
        # Older bd schemas used `type` instead of `issue_type`.
        raw = {"id": "x", "title": "T", "type": "bug", "dependencies": []}
        out = normalize_bead(raw, "x")
        self.assertEqual(out["type"], "bug")

    def test_non_dict_input_returns_error(self):
        out = normalize_bead("not a dict", "requested-id")
        self.assertIn("error", out)
        self.assertEqual(out["id"], "requested-id")

    def test_malformed_dependencies_ignored(self):
        raw = {
            "id": "b1",
            "title": "T",
            "dependencies": ["not-a-dict", None, {"type": "blocks"}],
        }
        out = normalize_bead(raw, "b1")
        # Should not crash; blocks/blocked_by stay empty.
        self.assertEqual(out["blocks"], [])
        self.assertEqual(out["blocked_by"], [])


class TestFetchBead(unittest.TestCase):
    def test_success_with_array_response(self):
        stdout = json.dumps(
            [
                {
                    "id": "abc-1",
                    "title": "Do thing",
                    "status": "open",
                    "priority": 2,
                    "issue_type": "task",
                    "dependencies": [],
                }
            ]
        )
        mock_run = MagicMock(return_value=_mk_result(stdout=stdout))
        out = fetch_bead("abc-1", run=mock_run)
        self.assertEqual(out["id"], "abc-1")
        self.assertEqual(out["title"], "Do thing")
        self.assertEqual(out["type"], "task")
        self.assertNotIn("error", out)
        mock_run.assert_called_once()

    def test_success_with_object_response(self):
        # Future-proofing: if bd ever returns a bare object, we cope.
        stdout = json.dumps(
            {"id": "abc-1", "title": "One", "status": "closed", "dependencies": []}
        )
        mock_run = MagicMock(return_value=_mk_result(stdout=stdout))
        out = fetch_bead("abc-1", run=mock_run)
        self.assertEqual(out["id"], "abc-1")
        self.assertEqual(out["status"], "closed")

    def test_empty_array_is_error(self):
        mock_run = MagicMock(return_value=_mk_result(stdout="[]"))
        out = fetch_bead("abc-1", run=mock_run)
        self.assertIn("error", out)

    def test_empty_id(self):
        mock_run = MagicMock()
        out = fetch_bead("   ", run=mock_run)
        self.assertIn("error", out)
        mock_run.assert_not_called()

    def test_bd_nonzero_exit(self):
        mock_run = MagicMock(
            return_value=_mk_result(stdout="", stderr="not found", returncode=1)
        )
        out = fetch_bead("xyz-0", run=mock_run)
        self.assertEqual(out["error"], "not found")

    def test_bd_invalid_json(self):
        mock_run = MagicMock(return_value=_mk_result(stdout="garbage", returncode=0))
        out = fetch_bead("xyz-0", run=mock_run)
        self.assertIn("invalid bd JSON", out["error"])

    def test_subprocess_raises(self):
        mock_run = MagicMock(side_effect=OSError("ENOENT bd"))
        out = fetch_bead("xyz-0", run=mock_run)
        self.assertIn("OSError", out["error"])


class TestRunCli(unittest.TestCase):
    def test_empty_exits_2(self):
        with patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=io.StringIO()):
            rc = run_cli([])
        self.assertEqual(rc, 2)

    def test_fan_out_preserves_order(self):
        responses = {
            "a-1": [{"id": "a-1", "title": "A", "dependencies": []}],
            "b-2": [{"id": "b-2", "title": "B", "dependencies": []}],
            "c-3": [{"id": "c-3", "title": "C", "dependencies": []}],
        }

        def fake_run(cmd, **kwargs):
            # cmd = ['bd', 'show', '<id>', '--json']
            bid = cmd[2]
            return _mk_result(stdout=json.dumps(responses[bid]))

        captured = io.StringIO()
        with patch("chop_bulk.bd_show.subprocess.run", side_effect=fake_run), \
             patch("sys.stdout", new=captured), \
             patch("sys.stderr", new=io.StringIO()):
            rc = run_cli(["a-1", "b-2", "c-3"], max_workers=3)
        self.assertEqual(rc, 0)
        parsed = json.loads(captured.getvalue())
        self.assertEqual([e["id"] for e in parsed], ["a-1", "b-2", "c-3"])
        self.assertEqual([e["title"] for e in parsed], ["A", "B", "C"])

    def test_partial_failure_inlined(self):
        def fake_run(cmd, **kwargs):
            bid = cmd[2]
            if bid == "b-2":
                return _mk_result(stdout="", stderr="not found", returncode=1)
            return _mk_result(stdout=json.dumps([{"id": bid, "title": bid, "dependencies": []}]))

        captured = io.StringIO()
        with patch("chop_bulk.bd_show.subprocess.run", side_effect=fake_run), \
             patch("sys.stdout", new=captured), \
             patch("sys.stderr", new=io.StringIO()):
            rc = run_cli(["a-1", "b-2", "c-3"], max_workers=2)
        self.assertEqual(rc, 0)
        parsed = json.loads(captured.getvalue())
        self.assertEqual(len(parsed), 3)
        self.assertNotIn("error", parsed[0])
        self.assertIn("error", parsed[1])
        self.assertEqual(parsed[1]["id"], "b-2")
        self.assertNotIn("error", parsed[2])


if __name__ == "__main__":
    unittest.main()
