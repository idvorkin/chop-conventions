#!/usr/bin/env python3
"""Unit tests for diagnose.py pure functions.

Run with: python3 -m unittest test_diagnose.py
"""

import sys
import unittest
from pathlib import Path

# Make sibling diagnose.py importable
sys.path.insert(0, str(Path(__file__).parent))

from diagnose import (  # noqa: E402
    Remote,
    classify_remotes,
    is_fork_url,
    parse_cherry_leftovers,
    parse_remotes,
)

FORK_ORGS = ["idvorkin-ai-tools"]


class TestParseRemotes(unittest.TestCase):
    def test_single_remote_dedups_fetch_and_push(self):
        raw = (
            "origin\tgit@github.com:idvorkin/chop.git (fetch)\n"
            "origin\tgit@github.com:idvorkin/chop.git (push)\n"
        )
        self.assertEqual(
            parse_remotes(raw),
            [Remote("origin", "git@github.com:idvorkin/chop.git")],
        )

    def test_two_remotes(self):
        raw = (
            "origin\tgit@github.com:idvorkin-ai-tools/chop.git (fetch)\n"
            "origin\tgit@github.com:idvorkin-ai-tools/chop.git (push)\n"
            "upstream\tgit@github.com:idvorkin/chop.git (fetch)\n"
            "upstream\tgit@github.com:idvorkin/chop.git (push)\n"
        )
        result = parse_remotes(raw)
        self.assertEqual(len(result), 2)
        self.assertIn(
            Remote("origin", "git@github.com:idvorkin-ai-tools/chop.git"), result
        )
        self.assertIn(Remote("upstream", "git@github.com:idvorkin/chop.git"), result)

    def test_empty_output(self):
        self.assertEqual(parse_remotes(""), [])


class TestIsForkUrl(unittest.TestCase):
    def test_ssh_url_matches(self):
        self.assertTrue(
            is_fork_url("git@github.com:idvorkin-ai-tools/foo.git", FORK_ORGS)
        )

    def test_https_url_matches(self):
        self.assertTrue(
            is_fork_url("https://github.com/idvorkin-ai-tools/foo", FORK_ORGS)
        )

    def test_non_fork_does_not_match(self):
        self.assertFalse(is_fork_url("git@github.com:idvorkin/foo.git", FORK_ORGS))

    def test_substring_does_not_false_match(self):
        # A user org named "idvorkin" should NOT match fork org "idvorkin-ai-tools"
        self.assertFalse(
            is_fork_url(
                "git@github.com:idvorkin/idvorkin-ai-tools-plugin.git", FORK_ORGS
            )
        )


class TestClassifyRemotes(unittest.TestCase):
    def test_single_canonical_origin_is_clean(self):
        remotes = [Remote("origin", "git@github.com:idvorkin/foo.git")]
        result = classify_remotes(remotes, FORK_ORGS)
        self.assertEqual(result.source, "origin")
        self.assertFalse(result.is_fork_workflow)
        self.assertEqual(result.issues, [])

    def test_proper_fork_workflow_is_clean(self):
        remotes = [
            Remote("origin", "git@github.com:idvorkin-ai-tools/foo.git"),
            Remote("upstream", "git@github.com:idvorkin/foo.git"),
        ]
        result = classify_remotes(remotes, FORK_ORGS)
        self.assertEqual(result.source, "upstream")
        self.assertTrue(result.is_fork_workflow)
        self.assertEqual(result.issues, [])

    def test_non_standard_remote_name_flagged(self):
        remotes = [
            Remote("origin", "git@github.com:idvorkin/foo.git"),
            Remote("fork", "git@github.com:idvorkin-ai-tools/foo.git"),
        ]
        result = classify_remotes(remotes, FORK_ORGS)
        kinds = [i.kind for i in result.issues]
        self.assertIn("non_standard_name", kinds)

    def test_swapped_remotes_flagged(self):
        # origin points at canonical; upstream points at fork — backwards
        remotes = [
            Remote("origin", "git@github.com:idvorkin/foo.git"),
            Remote("upstream", "git@github.com:idvorkin-ai-tools/foo.git"),
        ]
        result = classify_remotes(remotes, FORK_ORGS)
        kinds = [i.kind for i in result.issues]
        self.assertIn("swapped_remotes", kinds)

    def test_lone_fork_remote_flagged(self):
        # Only a fork exists; no canonical remote to PR against
        remotes = [Remote("origin", "git@github.com:idvorkin-ai-tools/foo.git")]
        result = classify_remotes(remotes, FORK_ORGS)
        kinds = [i.kind for i in result.issues]
        self.assertIn("fork_without_canonical", kinds)

    def test_issue_includes_fix_command(self):
        remotes = [
            Remote("origin", "git@github.com:idvorkin/foo.git"),
            Remote("upstream", "git@github.com:idvorkin-ai-tools/foo.git"),
        ]
        result = classify_remotes(remotes, FORK_ORGS)
        for issue in result.issues:
            self.assertTrue(issue.fix, f"issue {issue.kind} missing fix command")


class TestParseCherryLeftovers(unittest.TestCase):
    def test_keeps_only_patch_unique_commits(self):
        raw = (
            "- 1234567 already upstream under different sha\n"
            "+ 89abcde follow-up work still missing upstream\n"
        )
        self.assertEqual(
            parse_cherry_leftovers(raw),
            ["89abcde follow-up work still missing upstream"],
        )

    def test_empty_output(self):
        self.assertEqual(parse_cherry_leftovers(""), [])


if __name__ == "__main__":
    unittest.main()
