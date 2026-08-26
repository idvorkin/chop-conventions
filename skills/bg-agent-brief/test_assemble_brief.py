#!/usr/bin/env python3
"""Unit tests for assemble_brief.py pure functions.

Run with: python3 -m unittest test_assemble_brief

Typer is NOT imported here — the CLI is wired in `_build_app()` behind the
`if __name__ == "__main__":` guard, so these tests import the pure
assembly layer in system Python without `ModuleNotFoundError`. No
subprocess mocking: the functions under test are string builders with no
I/O.
"""

import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import assemble_brief  # noqa: E402
from assemble_brief import (  # noqa: E402
    CLEANUP_LINE,
    PUSH_TARGETS,
    assemble_brief as build_brief,
    assemble_followup,
    push_rules_block,
)


class TestPushRulesBlock(unittest.TestCase):
    def test_all_targets_render_without_error(self):
        for target in PUSH_TARGETS:
            block = push_rules_block(target, repo_slug="o/r", branch="feat")
            self.assertTrue(block.startswith("# Repo push rules"))

    def test_unknown_target_raises(self):
        with self.assertRaises(ValueError):
            push_rules_block("gitlab")

    def test_bitbucket_uses_browser_url_not_gh(self):
        block = push_rules_block(
            "bitbucket", repo_slug="idvorkin/igor2", branch="my-feat"
        )
        self.assertIn("does NOT work", block)
        self.assertIn(
            "https://bitbucket.org/idvorkin/igor2/pull-requests/new"
            "?source=my-feat&dest=main",
            block,
        )

    def test_bitbucket_honors_default_branch(self):
        block = push_rules_block(
            "bitbucket", repo_slug="idvorkin/igor2", default_branch="master"
        )
        self.assertIn("dest=master", block)

    def test_github_fork_has_repo_flag(self):
        block = push_rules_block("github-fork", repo_slug="idvorkin/chop")
        self.assertIn("gh pr create --repo idvorkin/chop", block)

    def test_github_direct_has_no_repo_flag(self):
        block = push_rules_block("github-direct")
        self.assertIn("gh pr create", block)
        # Explicitly tells the agent NOT to pass a --repo target.
        self.assertIn("no `--repo` flag", block)
        self.assertNotIn("--repo <", block)

    def test_none_target_says_no_push(self):
        block = push_rules_block("none")
        self.assertIn("No push or PR", block)

    def test_never_merge_rule_present_for_pushing_targets(self):
        for target in ("github-fork", "github-direct", "bitbucket"):
            block = push_rules_block(target, repo_slug="o/r")
            self.assertIn("NEVER `gh pr merge`", block)
            self.assertIn("main/master", block)


class TestAssembleBrief(unittest.TestCase):
    def test_minimal_brief_has_all_mandatory_sections(self):
        out = build_brief(role="Ship the thing.")
        self.assertIn("You are a background agent. Ship the thing.", out)
        self.assertIn("# VERIFY BEFORE YOU REPORT DONE", out)
        self.assertIn("# Repo push rules", out)
        self.assertIn("# Report contract", out)

    def test_optional_sections_omitted_when_empty(self):
        out = build_brief(role="x")
        self.assertNotIn("# READ FIRST", out)
        self.assertNotIn("# Constraints", out)
        self.assertNotIn("# The work", out)

    def test_read_first_is_ordered_numbered(self):
        out = build_brief(
            role="x", read_first=["CLAUDE.md", "README.md", "bead igor2-9"]
        )
        self.assertIn("# READ FIRST, in order", out)
        self.assertIn("1. CLAUDE.md", out)
        self.assertIn("2. README.md", out)
        self.assertIn("3. bead igor2-9", out)

    def test_work_is_numbered(self):
        out = build_brief(role="x", work=["do A", "do B"])
        self.assertIn("# The work", out)
        self.assertIn("1. do A", out)
        self.assertIn("2. do B", out)

    def test_constraints_are_bulleted(self):
        out = build_brief(role="x", constraints=["no worktree", "do not edit foo"])
        self.assertIn("# Constraints", out)
        self.assertIn("- no worktree", out)
        self.assertIn("- do not edit foo", out)

    def test_cleanup_on_by_default(self):
        out = build_brief(role="x")
        self.assertIn(CLEANUP_LINE, out)
        self.assertIn("igor2-88g.114", out)

    def test_cleanup_can_be_disabled(self):
        out = build_brief(role="x", cleanup=False)
        self.assertNotIn(CLEANUP_LINE, out)

    def test_verify_checks_rendered_when_present(self):
        out = build_brief(role="x", verify=["curl -sf localhost -> 200"])
        self.assertIn("- curl -sf localhost -> 200", out)

    def test_default_report_contract_when_none_given(self):
        out = build_brief(role="x")
        self.assertIn("The PR URL on its own line", out)

    def test_custom_report_contract(self):
        out = build_brief(role="x", report=["PR URL", "screenshot at 390px"])
        self.assertIn("- PR URL", out)
        self.assertIn("- screenshot at 390px", out)

    def test_worktree_injects_cwd_and_git_note(self):
        out = build_brief(role="x", worktree="/tmp/wt")
        self.assertIn("cd /tmp/wt", out)
        self.assertIn("cwd does NOT persist", out)
        self.assertIn(".git/", out)

    def test_no_worktree_no_cwd_note(self):
        out = build_brief(role="x")
        self.assertNotIn("cwd does NOT persist", out)

    def test_section_order(self):
        out = build_brief(
            role="x",
            read_first=["a"],
            constraints=["b"],
            work=["c"],
            verify=["d"],
            report=["e"],
        )
        order = [
            out.index("You are a background agent"),
            out.index("# READ FIRST"),
            out.index("# Constraints"),
            out.index("# The work"),
            out.index("# VERIFY BEFORE YOU REPORT DONE"),
            out.index("# Repo push rules"),
            out.index("# Report contract"),
        ]
        self.assertEqual(order, sorted(order))

    def test_bitbucket_end_to_end(self):
        out = build_brief(
            role="Fix the typo in igor2.",
            push="bitbucket",
            repo_slug="idvorkin/igor2",
            default_branch="master",
            branch="fix-typo",
        )
        self.assertIn("bitbucket.org/idvorkin/igor2", out)
        self.assertIn("dest=master", out)


class TestAssembleFollowup(unittest.TestCase):
    def test_kinds_render_correct_header(self):
        cases = {
            "additional-requirement": "ADDITIONAL REQUIREMENT",
            "scope-reduction": "SCOPE REDUCTION",
            "governing-principle": "GOVERNING DESIGN PRINCIPLE",
        }
        for kind, header in cases.items():
            out = assemble_followup(kind, "keep it simple")
            self.assertIn(header, out)
            self.assertIn("verbatim: 'keep it simple'", out)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            assemble_followup("random", "x")

    def test_followup_says_do_not_restart(self):
        out = assemble_followup("scope-reduction", "only the header")
        self.assertIn("do not restart", out)


if __name__ == "__main__":
    unittest.main()
