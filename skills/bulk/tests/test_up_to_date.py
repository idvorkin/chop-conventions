"""Unit tests for bulk-up-to-date's diagnose_repo.

subprocess.run and the diagnose resolver are always faked: no real
diagnose run, no git calls.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from chop_bulk.up_to_date import diagnose_repo  # noqa: E402


def _ok(stdout: str) -> MagicMock:
    m = MagicMock()
    m.stdout, m.stderr, m.returncode = stdout, "", 0
    return m


class TestDiagnoseRepo(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name)
        (self.repo / ".git").mkdir()
        self.addCleanup(self._td.cleanup)

    def test_patched_module_globals_are_used(self):
        """Defaults resolve at call time, so patching the module works."""
        with (
            patch(
                "chop_bulk.up_to_date.resolve_diagnose_cmd", return_value=["fake-diag"]
            ) as resolve,
            patch(
                "chop_bulk.up_to_date.subprocess.run", return_value=_ok('{"a": 1}')
            ) as run,
        ):
            out = diagnose_repo(str(self.repo))
        resolve.assert_called_once_with()
        self.assertEqual(run.call_args[0][0], ["fake-diag"])
        self.assertEqual(run.call_args[1]["cwd"], str(self.repo))
        self.assertEqual(out, {"repo": str(self.repo), "diagnose_json": {"a": 1}})

    def test_injected_callables_win(self):
        run = MagicMock(return_value=_ok("{}"))
        out = diagnose_repo(str(self.repo), run=run, resolve=lambda: ["x"])
        self.assertEqual(run.call_args[0][0], ["x"])
        self.assertEqual(out["diagnose_json"], {})

    def test_resolver_missing_is_per_item_error(self):
        def missing():
            raise FileNotFoundError("no diagnose")

        run = MagicMock()
        out = diagnose_repo(str(self.repo), run=run, resolve=missing)
        self.assertEqual(out["error"], "no diagnose")
        run.assert_not_called()

    def test_not_a_git_repo(self):
        with tempfile.TemporaryDirectory() as td:
            out = diagnose_repo(td, run=MagicMock(), resolve=lambda: ["x"])
        self.assertIn("not a git repository", out["error"])


if __name__ == "__main__":
    unittest.main()
