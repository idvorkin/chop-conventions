"""Unit tests for bulk-bd-show.

Mocks subprocess.run so no real `bd` calls happen. Covers:
    - normalize_bead slices parent/blocks/blocked_by out of
      dependencies/dependents using bd's REAL schema.
    - fetch_bead unwraps `bd show --json`'s single-element array.
    - fetch_bead handles the object-not-array response shape too.
    - bd nonzero exit handled as per-item error.
    - run_cli preserves input order on fan-out.

Dependency fixtures mirror real payloads captured 2026-07-21 from
bd 1.0.5 via `bd show <id> --json` / `bd show <id> --json
--include-dependents` against a sandbox beads db: `dependencies` and
`dependents` are lists of FULL ISSUE OBJECTS carrying `id` +
`dependency_type` (no `{type, source, target}` edge shape exists), and
bd emits a top-level `parent` field on children of a parent-child dep.
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

    def test_blocks_dep_routes_to_blocked_by(self):
        # Mirrors `bd show sandbox-5k6 --json` (bd 1.0.5): a
        # dependency_type "blocks" entry under `dependencies` is a bead
        # THIS bead depends on, i.e. one blocking it.
        raw = {
            "id": "sandbox-5k6",
            "title": "Blocked bead",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "dependencies": [
                {
                    "id": "sandbox-eh3",
                    "title": "Blocker bead",
                    "status": "open",
                    "priority": 2,
                    "issue_type": "task",
                    "dependency_type": "blocks",
                }
            ],
            "dependent_count": 0,
            "dependency_count": 1,
        }
        out = normalize_bead(raw, "sandbox-5k6")
        self.assertEqual(out["blocked_by"], ["sandbox-eh3"])
        self.assertEqual(out["blocks"], [])
        self.assertIsNone(out["parent"])

    def test_parent_child_dep_sets_parent(self):
        # Mirrors `bd show sandbox-cal --json` (bd 1.0.5): the child's
        # `dependencies` holds the parent with dependency_type
        # "parent-child", AND bd emits a top-level `parent` field.
        raw = {
            "id": "sandbox-cal",
            "title": "Child bead",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "dependencies": [
                {
                    "id": "sandbox-f4p",
                    "title": "Parent epic",
                    "status": "open",
                    "priority": 2,
                    "issue_type": "epic",
                    "dependency_type": "parent-child",
                }
            ],
            "parent": "sandbox-f4p",
            "dependent_count": 0,
            "dependency_count": 1,
        }
        out = normalize_bead(raw, "sandbox-cal")
        self.assertEqual(out["parent"], "sandbox-f4p")
        self.assertEqual(out["blocks"], [])
        self.assertEqual(out["blocked_by"], [])

    def test_parent_derived_from_dep_when_top_level_absent(self):
        # Defensive: derive parent from the parent-child dep entry even
        # if bd omits the top-level `parent` field.
        raw = {
            "id": "sandbox-cal",
            "title": "Child bead",
            "dependencies": [
                {
                    "id": "sandbox-f4p",
                    "title": "Parent epic",
                    "dependency_type": "parent-child",
                },
            ],
        }
        out = normalize_bead(raw, "sandbox-cal")
        self.assertEqual(out["parent"], "sandbox-f4p")

    def test_dependents_route_to_blocks(self):
        # Mirrors `bd show sandbox-eh3 --json --include-dependents`
        # (bd 1.0.5): `dependents` lists beads that depend on this one;
        # its "blocks" entries are beads this bead blocks.
        raw = {
            "id": "sandbox-eh3",
            "title": "Blocker bead",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "dependents": [
                {
                    "id": "sandbox-5k6",
                    "title": "Blocked bead",
                    "status": "open",
                    "priority": 2,
                    "issue_type": "task",
                    "dependency_type": "blocks",
                }
            ],
            "dependent_count": 1,
            "dependency_count": 0,
        }
        out = normalize_bead(raw, "sandbox-eh3")
        self.assertEqual(out["blocks"], ["sandbox-5k6"])
        self.assertEqual(out["blocked_by"], [])
        self.assertIsNone(out["parent"])

    def test_unknown_dependency_type_ignored(self):
        raw = {
            "id": "b1",
            "title": "T",
            "dependencies": [
                {"id": "b2", "dependency_type": "related"},
                {"id": "b3", "dependency_type": "tracks"},
                {"id": "b4"},  # missing dependency_type entirely
            ],
            "dependents": [
                {"id": "b5", "dependency_type": "parent-child"},
            ],
        }
        out = normalize_bead(raw, "b1")
        self.assertIsNone(out["parent"])
        self.assertEqual(out["blocks"], [])
        self.assertEqual(out["blocked_by"], [])

    def test_dependencies_key_absent(self):
        # Real bd omits `dependencies`/`dependents` entirely when a bead
        # has none (captured: `bd show chop-conventions-jge --json`).
        raw = {
            "id": "chop-conventions-jge",
            "title": "bulk: bd_show parses fabricated dependency schema",
            "status": "open",
            "priority": 2,
            "issue_type": "bug",
            "dependent_count": 0,
            "dependency_count": 0,
        }
        out = normalize_bead(raw, "chop-conventions-jge")
        self.assertIsNone(out["parent"])
        self.assertEqual(out["blocks"], [])
        self.assertEqual(out["blocked_by"], [])

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
            "dependencies": ["not-a-dict", None, {"dependency_type": "blocks"}],
            "dependents": ["also-not-a-dict", {"dependency_type": "blocks"}],
        }
        out = normalize_bead(raw, "b1")
        # Should not crash; entries without an `id` are skipped.
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
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[:3], ["bd", "show", "abc-1"])
        self.assertIn("--json", cmd)
        # Without --include-dependents, `blocks` could never populate.
        self.assertIn("--include-dependents", cmd)

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
        with (
            patch("sys.stdout", new=io.StringIO()),
            patch("sys.stderr", new=io.StringIO()),
        ):
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
        with (
            patch("chop_bulk.bd_show.subprocess.run", side_effect=fake_run),
            patch("sys.stdout", new=captured),
            patch("sys.stderr", new=io.StringIO()),
        ):
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
            return _mk_result(
                stdout=json.dumps([{"id": bid, "title": bid, "dependencies": []}])
            )

        captured = io.StringIO()
        with (
            patch("chop_bulk.bd_show.subprocess.run", side_effect=fake_run),
            patch("sys.stdout", new=captured),
            patch("sys.stderr", new=io.StringIO()),
        ):
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
