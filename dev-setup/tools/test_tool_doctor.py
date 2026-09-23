#!/usr/bin/env python3
"""Unit tests for tool_doctor.py classification. No subprocess: checks are faked.

Run with: python3 -m unittest test_tool_doctor.py   (pytest collects these too)
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tool_doctor import (  # noqa: E402
    MISSING,
    OK,
    WARN,
    CheckResult,
    classify,
    exit_code,
    format_line,
    is_below,
    parse_version,
    run_check,
    summary,
)

ADOPTED = {
    "name": "treehouse",
    "kind": "binary",
    "install": "curl -fsSL https://example.test/install.sh | sh",
    "check": "treehouse --version",
    "min_version": "2.3.0",
    "status": "adopted",
}
PROPOSED = {
    "name": "lavish-axi",
    "kind": "agent-skill",
    "install": "npx skills add kunchenguid/lavish-axi --skill lavish",
    "check": 'test -d "$HOME/.agents/skills/lavish"',
    "status": "proposed",
}


class ParseVersionTests(unittest.TestCase):
    def test_extracts_from_real_world_output(self):
        cases = {
            "v2.3.0": "2.3.0",
            "0.1.44": "0.1.44",
            "bd version 1.1.2 (Homebrew)": "1.1.2",
            "rmux_helper 0.1.0 (4cefee0)": "0.1.0",
            "herdr 0.9.0": "0.9.0",
        }
        for output, expected in cases.items():
            with self.subTest(output=output):
                self.assertEqual(parse_version(output), expected)

    def test_no_version_present(self):
        self.assertIsNone(parse_version(""))
        self.assertIsNone(parse_version("installed"))


class IsBelowTests(unittest.TestCase):
    def test_older_is_below(self):
        self.assertTrue(is_below("1.0.9", "1.1.2"))
        self.assertTrue(is_below("2.3", "2.3.1"))

    def test_equal_or_newer_is_not(self):
        self.assertFalse(is_below("1.1.2", "1.1.2"))
        self.assertFalse(is_below("2.4.0", "2.3.0"))
        self.assertFalse(is_below("2.3.0.1", "2.3.0"))

    def test_unknown_version_never_warns(self):
        self.assertFalse(is_below(None, "1.0.0"))
        self.assertFalse(is_below("1.0.0", None))


class ClassifyTests(unittest.TestCase):
    def test_installed_reports_version(self):
        verdict = classify(ADOPTED, CheckResult(True, "v2.3.0"))
        self.assertEqual(verdict.state, OK)
        self.assertEqual(verdict.version, "2.3.0")
        self.assertEqual(verdict.detail, "")

    def test_installed_without_parsable_version(self):
        verdict = classify(PROPOSED, CheckResult(True, ""))
        self.assertEqual(verdict.state, OK)
        self.assertIsNone(verdict.version)

    def test_below_minimum_warns(self):
        verdict = classify(ADOPTED, CheckResult(True, "v2.1.0"))
        self.assertEqual(verdict.state, WARN)
        self.assertIn("2.3.0", verdict.detail)

    def test_missing_carries_install_command(self):
        verdict = classify(ADOPTED, CheckResult(False, "command not found"))
        self.assertEqual(verdict.state, MISSING)
        self.assertIn(ADOPTED["install"], verdict.detail)


class ExitCodeTests(unittest.TestCase):
    def test_missing_adopted_fails(self):
        verdicts = [(ADOPTED, classify(ADOPTED, CheckResult(False)))]
        self.assertEqual(exit_code(verdicts), 1)

    def test_missing_proposed_is_fine(self):
        verdicts = [(PROPOSED, classify(PROPOSED, CheckResult(False)))]
        self.assertEqual(exit_code(verdicts), 0)

    def test_outdated_adopted_is_not_fatal(self):
        verdicts = [(ADOPTED, classify(ADOPTED, CheckResult(True, "2.1.0")))]
        self.assertEqual(exit_code(verdicts), 0)

    def test_all_good(self):
        verdicts = [
            (ADOPTED, classify(ADOPTED, CheckResult(True, "2.3.0"))),
            (PROPOSED, classify(PROPOSED, CheckResult(True))),
        ]
        self.assertEqual(exit_code(verdicts), 0)


class FormattingTests(unittest.TestCase):
    def test_missing_line_shows_install(self):
        line = format_line(ADOPTED, classify(ADOPTED, CheckResult(False)))
        self.assertTrue(line.startswith("❌"))
        self.assertIn("treehouse", line)
        self.assertIn("curl -fsSL", line)

    def test_ok_line_has_no_trailing_detail(self):
        line = format_line(ADOPTED, classify(ADOPTED, CheckResult(True, "2.3.0")))
        self.assertTrue(line.startswith("✅"))
        self.assertEqual(line, line.rstrip())

    def test_summary_counts_states(self):
        verdicts = [
            (ADOPTED, classify(ADOPTED, CheckResult(True, "2.3.0"))),
            (ADOPTED, classify(ADOPTED, CheckResult(True, "2.1.0"))),
            (PROPOSED, classify(PROPOSED, CheckResult(False))),
        ]
        self.assertEqual(summary(verdicts), "1 installed, 1 outdated, 1 missing")


class RunCheckTests(unittest.TestCase):
    """`run=` injection, so no real command is ever executed here."""

    def test_zero_exit_passes(self):
        def fake_run(*_args, **_kwargs):
            class P:
                returncode = 0
                stdout = "v2.3.0"
                stderr = ""

            return P()

        result = run_check("treehouse --version", run=fake_run)
        self.assertTrue(result.passed)
        self.assertIn("2.3.0", result.output)

    def test_nonzero_exit_fails(self):
        def fake_run(*_args, **_kwargs):
            class P:
                returncode = 127
                stdout = ""
                stderr = "not found"

            return P()

        self.assertFalse(run_check("nope --version", run=fake_run).passed)

    def test_oserror_is_missing_not_crash(self):
        def fake_run(*_args, **_kwargs):
            raise OSError("boom")

        result = run_check("nope", run=fake_run)
        self.assertFalse(result.passed)
        self.assertIn("boom", result.output)


if __name__ == "__main__":
    unittest.main()
