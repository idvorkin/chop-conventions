"""Unit tests for pr-hygiene's classification logic.

No network: every test feeds a synthetic GraphQL `pullRequest` payload into the
pure `classify()` function and asserts the tier. The gh shell-outs are exercised
only via mocked `subprocess.run`.

Covers the tier boundaries that are the whole point of the tool:
    - green: no review activity
    - yellow: CodeRabbit auto-summary / CI comment only (naive-filter noise)
    - red: unresolved bot *finding* thread
    - red: unresolved human review thread (human ask)
    - red: reviewDecision == CHANGES_REQUESTED
    - red: human reviewer has the last word, author hasn't responded
    - NOT red: author already responded after the human comment (regression)
    - self-authored threads don't count as asks
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from pr_hygiene import (  # noqa: E402
    classify,
    gather_prs,
    has_red,
    is_bot,
    is_noise_comment,
    render_markdown,
    search_open_prs,
    sort_rows,
)


def _iso(days_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _comment(login, body="", days_ago=1.0, typename="User"):
    return {
        "author": {"login": login, "__typename": typename},
        "body": body,
        "createdAt": _iso(days_ago),
    }


def _review(login, state="COMMENTED", days_ago=1.0, typename="User"):
    return {
        "author": {"login": login, "__typename": typename},
        "state": state,
        "submittedAt": _iso(days_ago),
    }


def _thread(comments, resolved=False, outdated=False):
    return {
        "isResolved": resolved,
        "isOutdated": outdated,
        "comments": {"nodes": comments},
    }


def _pr(
    *,
    author="me",
    decision=None,
    push_days=5.0,
    threads=None,
    reviews=None,
    comments=None,
):
    return {
        "author": {"login": author, "__typename": "User"},
        "reviewDecision": decision,
        "updatedAt": _iso(1),
        "commits": {
            "nodes": [
                {"commit": {"committedDate": _iso(push_days), "pushedDate": None}}
            ]
        },
        "reviewThreads": {"nodes": threads or []},
        "reviews": {"nodes": reviews or []},
        "comments": {"nodes": comments or []},
    }


class TestBotDetection(unittest.TestCase):
    def test_bot_by_typename(self):
        self.assertTrue(is_bot("coderabbitai", "Bot"))

    def test_bot_by_suffix(self):
        self.assertTrue(is_bot("github-actions[bot]", "User"))

    def test_human_not_bot(self):
        # idvorkin-ai-tools is a real User account, not a bot
        self.assertFalse(is_bot("idvorkin-ai-tools", "User"))
        self.assertFalse(is_bot("igor", "User"))


class TestNoiseDetection(unittest.TestCase):
    def test_coderabbit_login_is_noise(self):
        self.assertTrue(is_noise_comment("coderabbitai", "Bot", "anything"))

    def test_ci_bot_is_noise(self):
        self.assertTrue(is_noise_comment("github-actions", "Bot", "build passed"))

    def test_walkthrough_body_is_noise(self):
        self.assertTrue(
            is_noise_comment("somebot", "Bot", "<!-- walkthrough_start -->")
        )

    def test_human_comment_not_noise(self):
        self.assertFalse(is_noise_comment("alice", "User", "please fix this"))


class TestClassifyGreen(unittest.TestCase):
    def test_no_activity_is_green(self):
        r = classify(_pr(), "me")
        self.assertEqual(r["tier"], "green")
        self.assertEqual(r["unresolved"], 0)


class TestClassifyYellow(unittest.TestCase):
    def test_coderabbit_summary_only_is_yellow(self):
        pr = _pr(
            comments=[
                _comment("coderabbitai", "summarize by coderabbit.ai", typename="Bot")
            ]
        )
        r = classify(pr, "me")
        self.assertEqual(r["tier"], "yellow")

    def test_ci_comment_only_is_yellow(self):
        pr = _pr(comments=[_comment("github-actions", "CI green", typename="Bot")])
        self.assertEqual(classify(pr, "me")["tier"], "yellow")

    def test_resolved_threads_only_is_yellow(self):
        pr = _pr(
            threads=[
                _thread(
                    [_comment("coderabbitai", "nit", typename="Bot")], resolved=True
                )
            ]
        )
        self.assertEqual(classify(pr, "me")["tier"], "yellow")


class TestClassifyRed(unittest.TestCase):
    def test_unresolved_bot_finding_is_red(self):
        pr = _pr(
            threads=[
                _thread([_comment("coderabbitai", "actual finding", typename="Bot")])
            ]
        )
        r = classify(pr, "me")
        self.assertEqual(r["tier"], "red")
        self.assertFalse(r["human_ask"])
        self.assertEqual(r["unresolved"], 1)

    def test_unresolved_human_thread_is_red_human_ask(self):
        pr = _pr(threads=[_thread([_comment("alice", "please change X")])])
        r = classify(pr, "me")
        self.assertEqual(r["tier"], "red")
        self.assertTrue(r["human_ask"])

    def test_changes_requested_is_red(self):
        pr = _pr(
            decision="CHANGES_REQUESTED",
            reviews=[_review("alice", state="CHANGES_REQUESTED", days_ago=2)],
        )
        self.assertEqual(classify(pr, "me")["tier"], "red")

    def test_human_last_word_post_push_is_red(self):
        # human commented 1 day ago, last push 5 days ago, author silent since
        pr = _pr(push_days=5, comments=[_comment("alice", "any update?", days_ago=1)])
        r = classify(pr, "me")
        self.assertEqual(r["tier"], "red")
        self.assertIn("last word", r["verdict"])

    def test_cross_identity_human_ask_is_red(self):
        # PR authored by idvorkin-ai-tools, reviewed inline by idvorkin (a human,
        # a *different* login) -> counts as a real human ask.
        pr = _pr(
            author="idvorkin-ai-tools",
            threads=[_thread([_comment("idvorkin", "put this into /time-off")])],
        )
        r = classify(pr, "idvorkin-ai-tools")
        self.assertEqual(r["tier"], "red")
        self.assertTrue(r["human_ask"])


class TestClassifyRegressions(unittest.TestCase):
    def test_author_responded_after_human_is_not_red(self):
        # Reviewer commented 3 days ago; author REPLIED 1 day ago -> author has
        # the last word -> not red. (This is the tweego#35 false-positive fix:
        # comparing only against last push wrongly kept it red.)
        pr = _pr(
            author="me",
            push_days=10,
            comments=[
                _comment("alice", "please clarify", days_ago=3),
                _comment("me", "done, see above", days_ago=1),
            ],
        )
        r = classify(pr, "me")
        self.assertNotEqual(r["tier"], "red")

    def test_author_pushed_after_human_is_not_red(self):
        # Reviewer commented 3 days ago; author pushed 1 day ago -> acted.
        pr = _pr(
            author="me", push_days=1, comments=[_comment("alice", "fix", days_ago=3)]
        )
        self.assertNotEqual(classify(pr, "me")["tier"], "red")

    def test_self_authored_thread_not_an_ask(self):
        # A thread whose only commenter is the PR author is a self-note, not an
        # external ask -> should not force red on its own.
        pr = _pr(author="me", threads=[_thread([_comment("me", "TODO refactor")])])
        r = classify(pr, "me")
        self.assertNotEqual(r["tier"], "red")

    def test_coderabbit_summary_in_thread_not_counted(self):
        # Defensive: an auto-summary body that somehow lands in a thread is noise.
        pr = _pr(
            threads=[
                _thread(
                    [
                        _comment(
                            "coderabbitai", "summarize by coderabbit.ai", typename="Bot"
                        )
                    ]
                )
            ]
        )
        self.assertNotEqual(classify(pr, "me")["tier"], "red")


class TestRenderAndSort(unittest.TestCase):
    def _row(self, tier, num, unres=0, days=1):
        return {
            "tier": tier,
            "repo": "o/r",
            "number": num,
            "title": "t",
            "url": f"https://x/{num}",
            "unresolved": unres,
            "human_ask": False,
            "verdict": "v",
            "last_actor": "a",
            "last_days": days,
        }

    def test_sort_red_first_then_unresolved_desc(self):
        rows = [
            self._row("green", 1),
            self._row("red", 2, unres=1),
            self._row("yellow", 3),
            self._row("red", 4, unres=9),
        ]
        ordered = sort_rows(rows)
        self.assertEqual([r["number"] for r in ordered], [4, 2, 3, 1])

    def test_render_contains_link_and_emoji(self):
        md = render_markdown([self._row("red", 7, unres=2)], [])
        self.assertIn("[o/r#7](https://x/7)", md)
        self.assertIn("🔴", md)
        self.assertIn("Bitbucket", md)

    def test_render_lists_errors(self):
        md = render_markdown([], ["search --author=x: boom"])
        self.assertIn("Could not query", md)
        self.assertIn("boom", md)

    def test_has_red(self):
        self.assertTrue(has_red([self._row("red", 1)]))
        self.assertFalse(has_red([self._row("yellow", 1), self._row("green", 2)]))


class TestGhShellOuts(unittest.TestCase):
    def test_search_open_prs_parses(self):
        payload = '[{"repository":{"nameWithOwner":"o/r"},"number":5,"title":"T","url":"u","updatedAt":"2026-01-01T00:00:00Z"}]'
        run = MagicMock(return_value=MagicMock(returncode=0, stdout=payload, stderr=""))
        rows = search_open_prs("me", run=run)
        self.assertEqual(rows[0]["repo"], "o/r")
        self.assertEqual(rows[0]["number"], 5)

    def test_search_open_prs_raises_on_failure(self):
        run = MagicMock(return_value=MagicMock(returncode=1, stdout="", stderr="boom"))
        with self.assertRaises(RuntimeError):
            search_open_prs("me", run=run)

    def test_gather_dedupes_across_authors(self):
        payload = '[{"repository":{"nameWithOwner":"o/r"},"number":5,"title":"T","url":"u","updatedAt":"z"}]'
        run = MagicMock(return_value=MagicMock(returncode=0, stdout=payload, stderr=""))
        prs, errors = gather_prs(["a", "b"], None, run=run)
        self.assertEqual(len(prs), 1)  # same PR found by both authors -> deduped
        self.assertEqual(errors, [])

    def test_gather_captures_search_error(self):
        run = MagicMock(return_value=MagicMock(returncode=1, stdout="", stderr="nope"))
        prs, errors = gather_prs(["a"], None, run=run)
        self.assertEqual(prs, [])
        self.assertEqual(len(errors), 1)

    def test_gather_repo_filter(self):
        payload = (
            '[{"repository":{"nameWithOwner":"o/keep"},"number":1,"title":"T","url":"u","updatedAt":"z"},'
            '{"repository":{"nameWithOwner":"o/drop"},"number":2,"title":"T","url":"u","updatedAt":"z"}]'
        )
        run = MagicMock(return_value=MagicMock(returncode=0, stdout=payload, stderr=""))
        prs, _ = gather_prs(["a"], "o/keep", run=run)
        self.assertEqual([p["repo"] for p in prs], ["o/keep"])


if __name__ == "__main__":
    unittest.main()
