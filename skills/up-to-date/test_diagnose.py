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
    CherryAnalysis,
    Remote,
    WorktreeRef,
    classify_remotes,
    is_fork_url,
    parse_cherry_status,
    parse_left_right_count,
    parse_remotes,
    parse_symbolic_ref_output,
    parse_worktree_list,
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


class TestParseCherryStatus(unittest.TestCase):
    def test_splits_unique_and_equivalent_commits(self):
        raw = (
            "- 1234567 already upstream under different sha\n"
            "+ 89abcde follow-up work still missing upstream\n"
        )
        self.assertEqual(
            parse_cherry_status(raw),
            CherryAnalysis(
                unique_commits=["89abcde follow-up work still missing upstream"],
                equivalent_commits=["1234567 already upstream under different sha"],
            ),
        )

    def test_empty_output(self):
        self.assertEqual(
            parse_cherry_status(""),
            CherryAnalysis(unique_commits=[], equivalent_commits=[]),
        )


class TestParseLeftRightCount(unittest.TestCase):
    def test_parses_tab_separated_counts(self):
        self.assertEqual(parse_left_right_count("3\t7"), (3, 7))

    def test_invalid_output_returns_none(self):
        self.assertIsNone(parse_left_right_count("nonsense"))


class TestParseWorktreeList(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(parse_worktree_list(""), [])

    def test_primary_only(self):
        raw = "worktree /home/user/repo\nHEAD abc123def456\nbranch refs/heads/main\n"
        self.assertEqual(
            parse_worktree_list(raw),
            [WorktreeRef(path="/home/user/repo", branch="main")],
        )

    def test_primary_plus_linked(self):
        raw = (
            "worktree /home/user/repo\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /home/user/repo/.worktrees/feature-1\n"
            "HEAD def456\n"
            "branch refs/heads/delegated/feature-1\n"
            "\n"
            "worktree /home/user/repo/.worktrees/feature-2\n"
            "HEAD ghi789\n"
            "branch refs/heads/delegated/feature-2\n"
        )
        self.assertEqual(
            parse_worktree_list(raw),
            [
                WorktreeRef(path="/home/user/repo", branch="main"),
                WorktreeRef(
                    path="/home/user/repo/.worktrees/feature-1",
                    branch="delegated/feature-1",
                ),
                WorktreeRef(
                    path="/home/user/repo/.worktrees/feature-2",
                    branch="delegated/feature-2",
                ),
            ],
        )

    def test_skips_detached_worktree(self):
        # Detached worktrees have no `branch` line — they should not appear
        # in the output since there's nothing to prune.
        raw = (
            "worktree /home/user/repo\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /home/user/repo/.worktrees/detached\n"
            "HEAD def456\n"
            "detached\n"
        )
        self.assertEqual(
            parse_worktree_list(raw),
            [WorktreeRef(path="/home/user/repo", branch="main")],
        )

    def test_skips_bare_worktree(self):
        # Bare worktrees have a `bare` marker instead of `HEAD`/`branch`.
        raw = (
            "worktree /home/user/repo.git\n"
            "bare\n"
            "\n"
            "worktree /home/user/repo\n"
            "HEAD abc123\n"
            "branch refs/heads/main\n"
        )
        self.assertEqual(
            parse_worktree_list(raw),
            [WorktreeRef(path="/home/user/repo", branch="main")],
        )

    def test_preserves_order(self):
        # Primary-first ordering matters — caller relies on index 0 being primary.
        raw = (
            "worktree /primary\n"
            "HEAD aaa\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /linked\n"
            "HEAD bbb\n"
            "branch refs/heads/feature\n"
        )
        result = parse_worktree_list(raw)
        self.assertEqual(result[0].path, "/primary")
        self.assertEqual(result[1].path, "/linked")

    def test_path_with_spaces_preserved(self):
        # `git worktree list --porcelain` doesn't quote paths — spaces are
        # preserved literally. Parser must take everything after "worktree ".
        raw = "worktree /home/user/my project\nHEAD abc123\nbranch refs/heads/main\n"
        self.assertEqual(
            parse_worktree_list(raw),
            [WorktreeRef(path="/home/user/my project", branch="main")],
        )

    def test_branch_refs_heads_prefix_stripped(self):
        raw = "worktree /repo\nHEAD abc\nbranch refs/heads/nested/feature-name\n"
        self.assertEqual(
            parse_worktree_list(raw)[0].branch,
            "nested/feature-name",
        )


class TestParseSymbolicRefOutput(unittest.TestCase):
    def test_origin_main(self):
        self.assertEqual(
            parse_symbolic_ref_output("refs/remotes/origin/main", "origin"),
            "main",
        )

    def test_upstream_master(self):
        self.assertEqual(
            parse_symbolic_ref_output("refs/remotes/upstream/master", "upstream"),
            "master",
        )

    def test_trailing_newline_stripped(self):
        self.assertEqual(
            parse_symbolic_ref_output("refs/remotes/origin/develop\n", "origin"),
            "develop",
        )

    def test_wrong_src_returns_none(self):
        # If the ref belongs to a different remote, parser must not match.
        self.assertIsNone(
            parse_symbolic_ref_output("refs/remotes/upstream/main", "origin")
        )

    def test_missing_prefix_returns_none(self):
        self.assertIsNone(parse_symbolic_ref_output("refs/heads/main", "origin"))

    def test_empty_branch_returns_none(self):
        # Defensive: if stripping the prefix leaves an empty string, return None.
        self.assertIsNone(parse_symbolic_ref_output("refs/remotes/origin/", "origin"))


if __name__ == "__main__":
    unittest.main()
