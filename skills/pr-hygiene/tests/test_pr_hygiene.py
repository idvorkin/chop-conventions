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
from unittest.mock import MagicMock, patch

_SKILL_DIR = Path(__file__).resolve().parent.parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

import pr_hygiene  # noqa: E402
from pr_hygiene import (  # noqa: E402
    classify,
    current_gh_login,
    exit_code,
    gather_prs,
    graphql_pr,
    has_red,
    is_bot,
    is_noise_comment,
    render_markdown,
    resolve_authors,
    run_cli,
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

    def test_author_thread_reply_after_human_is_not_red(self):
        # Same false-positive class as tweego#35, different response channel:
        # reviewer left an ISSUE comment 3 days ago; author replied inside a
        # review thread 1 day ago (thread since resolved), no push, no issue
        # comment -> author has the last word -> not red.
        pr = _pr(
            author="me",
            push_days=10,
            threads=[
                _thread(
                    [
                        _comment("alice", "what about edge case Y?", days_ago=4),
                        _comment("me", "handled, see test", days_ago=1),
                    ],
                    resolved=True,
                )
            ],
            comments=[_comment("alice", "any update?", days_ago=3)],
        )
        r = classify(pr, "me")
        self.assertNotEqual(r["tier"], "red")

    def test_author_thread_reply_older_than_human_comment_still_red(self):
        # Guard: an OLD thread reply must not clear a NEWER human comment.
        # Author replied in a thread 3 days ago; reviewer issue-commented 1 day
        # ago and the author has been silent since -> still red.
        pr = _pr(
            author="me",
            push_days=10,
            threads=[
                _thread(
                    [
                        _comment("alice", "what about edge case Y?", days_ago=4),
                        _comment("me", "handled, see test", days_ago=3),
                    ],
                    resolved=True,
                )
            ],
            comments=[_comment("alice", "any update?", days_ago=1)],
        )
        r = classify(pr, "me")
        self.assertEqual(r["tier"], "red")

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


class TestApproval(unittest.TestCase):
    def test_human_approval_after_push_is_not_red(self):
        # Approved 1 day ago, last push 5 days ago, author silent since: an
        # approval is the reviewer's last word but NOT an ask.
        pr = _pr(
            decision="APPROVED",
            push_days=5,
            reviews=[_review("alice", state="APPROVED", days_ago=1)],
        )
        r = classify(pr, "me")
        self.assertEqual(r["tier"], "yellow")
        self.assertEqual(r["approved_by"], "alice")
        self.assertIn("approved by alice", r["verdict"])

    def test_comment_after_approval_is_still_red(self):
        # Only the LATEST human event counts: approve, then a follow-up ask.
        pr = _pr(
            push_days=5,
            reviews=[_review("alice", state="APPROVED", days_ago=3)],
            comments=[_comment("alice", "actually, one more thing", days_ago=1)],
        )
        r = classify(pr, "me")
        self.assertEqual(r["tier"], "red")
        self.assertIsNone(r["approved_by"])

    def test_approval_does_not_hide_other_reviewers_ask(self):
        # alice asked 2 days ago, bob approved 1 day ago, author silent:
        # bob's approval must not mask alice's unanswered ask.
        pr = _pr(
            push_days=5,
            comments=[_comment("alice", "please fix X", days_ago=2)],
            reviews=[_review("bob", state="APPROVED", days_ago=1)],
        )
        r = classify(pr, "me")
        self.assertEqual(r["tier"], "red")
        self.assertIn("last word", r["verdict"])

    def test_approval_with_unresolved_thread_is_still_red(self):
        pr = _pr(
            push_days=5,
            reviews=[_review("alice", state="APPROVED", days_ago=1)],
            threads=[_thread([_comment("bob", "please change X", days_ago=2)])],
        )
        self.assertEqual(classify(pr, "me")["tier"], "red")

    def test_approval_with_changes_requested_decision_is_still_red(self):
        # alice approved last, but bob's CHANGES_REQUESTED still stands.
        pr = _pr(
            decision="CHANGES_REQUESTED",
            reviews=[
                _review("bob", state="CHANGES_REQUESTED", days_ago=3),
                _review("alice", state="APPROVED", days_ago=1),
            ],
        )
        self.assertEqual(classify(pr, "me")["tier"], "red")

    def test_commented_review_last_word_is_still_red(self):
        pr = _pr(push_days=5, reviews=[_review("alice", days_ago=1)])
        self.assertEqual(classify(pr, "me")["tier"], "red")


class TestYellowVerdictMakesNoMergeClaim(unittest.TestCase):
    def test_no_merge_ready_claim(self):
        # Mergeability / CI are never queried, so the verdict must not assert
        # them (CLAUDE.md: Measure, Don't Hardcode).
        for pr in (
            _pr(comments=[_comment("github-actions", "CI green", typename="Bot")]),
            _pr(reviews=[_review("alice", state="APPROVED", days_ago=1)]),
        ):
            r = classify(pr, "me")
            self.assertEqual(r["tier"], "yellow")
            self.assertNotIn("merge", r["verdict"].lower())
            self.assertNotIn("CI only", r["verdict"])


def _gql(pr):
    import json as _json

    return MagicMock(
        returncode=0,
        stdout=_json.dumps({"data": {"repository": {"pullRequest": pr}}}),
        stderr="",
    )


def _threads_page(n, has_next, cursor="C1"):
    return {
        "pageInfo": {
            "hasNextPage": has_next,
            "endCursor": cursor if has_next else None,
        },
        "nodes": [_thread([_comment("alice", f"ask {i}")]) for i in range(n)],
    }


class TestThreadPagination(unittest.TestCase):
    def test_single_page_makes_one_call(self):
        pr = _pr()
        pr["reviewThreads"] = _threads_page(2, False)
        run = MagicMock(return_value=_gql(pr))
        out = graphql_pr("o/r", 1, run=run)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(len(out["reviewThreads"]["nodes"]), 2)

    def test_follows_next_page(self):
        first = _pr()
        first["reviewThreads"] = _threads_page(100, True, cursor="CUR")
        second = {"reviewThreads": _threads_page(3, False)}
        run = MagicMock(side_effect=[_gql(first), _gql(second)])
        out = graphql_pr("o/r", 1, run=run)
        self.assertEqual(len(out["reviewThreads"]["nodes"]), 103)
        self.assertEqual(run.call_count, 2)
        self.assertIn("cursor=CUR", run.call_args_list[1].args[0])

    def test_cap_raises_instead_of_truncating(self):
        page = _pr()
        page["reviewThreads"] = _threads_page(1, True)
        run = MagicMock(return_value=_gql(page))
        with patch.object(pr_hygiene, "MAX_THREAD_PAGES", 3):
            with self.assertRaises(RuntimeError):
                graphql_pr("o/r", 1, run=run)
        self.assertEqual(run.call_count, 3)

    def test_missing_page_info_is_single_page(self):
        # Older/synthetic payloads without pageInfo must still work.
        run = MagicMock(return_value=_gql(_pr()))
        self.assertEqual(graphql_pr("o/r", 1, run=run)["reviewThreads"]["nodes"], [])


class TestResolveAuthors(unittest.TestCase):
    def test_cli_wins(self):
        run = MagicMock()
        self.assertEqual(
            resolve_authors(["x"], env={"PR_HYGIENE_AUTHORS": "e"}, run=run), ["x"]
        )
        run.assert_not_called()

    def test_env_beats_gh(self):
        run = MagicMock()
        self.assertEqual(
            resolve_authors(None, env={"PR_HYGIENE_AUTHORS": "a, b,,"}, run=run),
            ["a", "b"],
        )
        run.assert_not_called()

    def test_falls_back_to_gh_user(self):
        run = MagicMock(
            return_value=MagicMock(returncode=0, stdout="octocat\n", stderr="")
        )
        self.assertEqual(resolve_authors(None, env={}, run=run), ["octocat"])
        self.assertEqual(run.call_args.args[0][:3], ["gh", "api", "user"])

    def test_gh_failure_raises(self):
        run = MagicMock(
            return_value=MagicMock(returncode=1, stdout="", stderr="not logged in")
        )
        with self.assertRaises(RuntimeError):
            current_gh_login(run=run)

    def test_gh_missing_raises_runtime_error(self):
        run = MagicMock(side_effect=FileNotFoundError("gh"))
        with self.assertRaises(RuntimeError):
            current_gh_login(run=run)

    def test_empty_login_raises(self):
        run = MagicMock(return_value=MagicMock(returncode=0, stdout="\n", stderr=""))
        with self.assertRaises(RuntimeError):
            resolve_authors(None, env={}, run=run)


class TestExitCode(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(exit_code([{"tier": "green"}], [], gate=True), 0)

    def test_red(self):
        self.assertEqual(exit_code([{"tier": "red"}], [], gate=True), 1)

    def test_errors_fail_closed_even_with_no_rows(self):
        self.assertEqual(exit_code([], ["search failed"], gate=True), 2)

    def test_errors_take_precedence_over_red(self):
        self.assertEqual(exit_code([{"tier": "red"}], ["boom"], gate=True), 2)

    def test_no_fail_always_zero(self):
        self.assertEqual(exit_code([{"tier": "red"}], ["boom"], gate=False), 0)


class TestRunCliFailsClosed(unittest.TestCase):
    """End-to-end through run_cli with subprocess.run patched — no real gh."""

    SEARCH = (
        '[{"repository":{"nameWithOwner":"o/r"},"number":5,"title":"T",'
        '"url":"u","updatedAt":"2026-01-01T00:00:00Z"}]'
    )

    def _run(self, fake, authors=("me",), fail_on_red=True):
        with (
            patch.object(pr_hygiene.subprocess, "run", side_effect=fake) as run,
            patch.object(pr_hygiene.sys, "stdout"),
            patch.object(pr_hygiene.sys, "stderr") as err,
            patch.dict(pr_hygiene.os.environ, {}, clear=False),
        ):
            pr_hygiene.os.environ.pop("PR_HYGIENE_AUTHORS", None)
            code = run_cli(
                list(authors) if authors else None,
                None,
                as_json=True,
                fail_on_red=fail_on_red,
                max_workers=1,
            )
            stderr = "".join(c.args[0] for c in err.write.call_args_list)
        return code, stderr, run

    def test_search_failure_exits_nonzero(self):
        fail = MagicMock(returncode=1, stdout="", stderr="gh: not logged in")
        code, stderr, _ = self._run(lambda *a, **k: fail)
        self.assertEqual(code, 2)
        self.assertIn("not logged in", stderr)
        self.assertIn("INCOMPLETE", stderr)

    def test_partial_search_failure_exits_nonzero(self):
        ok = MagicMock(returncode=0, stdout="[]", stderr="")
        fail = MagicMock(returncode=1, stdout="", stderr="boom")

        def fake(cmd, **k):
            return fail if "--author=bad" in cmd else ok

        code, _, _ = self._run(fake, authors=("good", "bad"))
        self.assertEqual(code, 2)

    def test_pr_fetch_failure_exits_nonzero(self):
        search = MagicMock(returncode=0, stdout=self.SEARCH, stderr="")
        fail = MagicMock(returncode=1, stdout="", stderr="HTTP 502")

        def fake(cmd, **k):
            return search if cmd[1] == "search" else fail

        code, stderr, _ = self._run(fake)
        self.assertEqual(code, 2)
        self.assertIn("o/r#5", stderr)

    def test_author_lookup_failure_exits_nonzero(self):
        fail = MagicMock(returncode=1, stdout="", stderr="gh: not logged in")
        code, stderr, run = self._run(lambda *a, **k: fail, authors=None)
        self.assertEqual(code, 2)
        self.assertIn("could not determine author", stderr)
        self.assertEqual(run.call_count, 1)  # no search attempted

    def test_no_fail_overrides_errors(self):
        fail = MagicMock(returncode=1, stdout="", stderr="boom")
        code, _, _ = self._run(lambda *a, **k: fail, fail_on_red=False)
        self.assertEqual(code, 0)

    def test_clean_run_exits_zero(self):
        search = MagicMock(returncode=0, stdout=self.SEARCH, stderr="")

        def fake(cmd, **k):
            return search if cmd[1] == "search" else _gql(_pr())

        code, _, _ = self._run(fake)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
